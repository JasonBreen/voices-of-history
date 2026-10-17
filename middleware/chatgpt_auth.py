"""Official Sign in with ChatGPT flow for this local, open-source client.

Credentials are encrypted with Windows DPAPI for the current Windows user.  This
module never reads Codex/ChatGPT application credentials or uses an API key.
"""

import argparse
import base64
import ctypes
from ctypes import wintypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import msvcrt
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

import jwt
import requests


ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
APP_NAME = "Voices of History"
CALLBACK_PATH = "/auth/callback"


class ChatGPTAuthError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True) if os.name == "nt" else None
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
if _crypt32:
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DataBlob), ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))), buf


def _protect(data):
    if not _crypt32:
        raise ChatGPTAuthError("Windows DPAPI is required for credential storage.")
    source, keepalive = _blob(data)
    result = _DataBlob()
    if not _crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None,
                                     1, ctypes.byref(result)):
        raise ChatGPTAuthError("Windows could not encrypt ChatGPT credentials.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(result.pbData)


def _unprotect(data):
    if not _crypt32:
        raise ChatGPTAuthError("Windows DPAPI is required for credential storage.")
    source, keepalive = _blob(data)
    result = _DataBlob()
    description = wintypes.LPWSTR()
    if not _crypt32.CryptUnprotectData(ctypes.byref(source),
                                       ctypes.byref(description), None, None, None,
                                       1, ctypes.byref(result)):
        raise ChatGPTAuthError("ChatGPT credentials could not be decrypted by this Windows user.")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(result.pbData)
        if description:
            _kernel32.LocalFree(description)


class ChatGPTAuth:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or Path(__file__).parent / "user-data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.credential_file = self.data_dir / "chatgpt-credentials.dpapi"
        self.lock_file = self.data_dir / "chatgpt-credentials.lock"

    def _locked(self):
        """Hold a Windows file lock so rotating refresh tokens cannot race."""
        from contextlib import contextmanager

        @contextmanager
        def lock():
            with open(self.lock_file, "a+b") as f:
                if f.tell() == 0:
                    f.write(b"0")
                    f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return lock()

    def _read(self):
        if not self.credential_file.exists():
            return {"host_id": None, "accounts": {}, "active_client_id": None}
        return json.loads(_unprotect(self.credential_file.read_bytes()).decode("utf-8"))

    def _write(self, data):
        encrypted = _protect(json.dumps(data, separators=(",", ":")).encode("utf-8"))
        fd, name = tempfile.mkstemp(prefix="chatgpt-", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encrypted)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.credential_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _host_id(self):
        with self._locked():
            data = self._read()
            if not data.get("host_id"):
                data["host_id"] = "urn:uuid:" + str(uuid.uuid4())
                self._write(data)
            return data["host_id"]

    def status(self):
        with self._locked():
            data = self._read()
            active = data.get("active_client_id")
            record = data.get("accounts", {}).get(active) if active else None
            if not record:
                return {"connected": False}
            return {"connected": True, "email": record.get("email", ""),
                    "client_id": active, "expires_at": record.get("expires_at", 0),
                    "plan_enabled": "chatgpt.tokens.use.direct" in record.get("scopes", [])}

    def login(self, timeout=300):
        """Open the system browser; only the user can grant plan access there."""
        host_id = self._host_id()
        with self._locked():
            data = self._read()
            active = data.get("active_client_id")
            prior = data.get("accounts", {}).get(active) if active else None
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                target = urlsplit(self.path)
                if target.path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                received.update({k: v[0] for k, v in parse_qs(target.query).items() if v})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Sign-in response received. You can return to Voices of History.")

            def log_message(self, *_args):
                pass  # Never log authorization codes or callback URLs.

        server = HTTPServer(("127.0.0.1", 0), Handler)
        server.timeout = 1
        redirect_uri = f"http://127.0.0.1:{server.server_port}{CALLBACK_PATH}"
        client_id = prior["client_id"] if prior else "dynamic_agent_client"
        params = {"client_id": client_id, "ext_agent_host_id": host_id,
                  "response_type": "code", "redirect_uri": redirect_uri,
                  "scope": SCOPES, "resource": RESOURCE, "state": state,
                  "nonce": nonce, "code_challenge_method": "S256",
                  "code_challenge": challenge}
        if prior:
            if prior.get("id_token"):
                params["id_token_hint"] = prior["id_token"]
            if prior.get("email"):
                params["login_hint"] = prior["email"]
        else:
            params["agent_name_hint"] = APP_NAME
        try:
            if not webbrowser.open(AUTHORIZE_URL + "?" + urlencode(params)):
                raise ChatGPTAuthError("Could not open a browser for ChatGPT sign-in.")
            print("Complete 'Continue with ChatGPT' in your browser. Waiting for the local callback...")
            deadline = time.monotonic() + timeout
            while not received and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not received:
            raise ChatGPTAuthError("Sign-in timed out. Run chatgpt_auth.py login again.")
        if not secrets.compare_digest(received.get("state", ""), state):
            raise ChatGPTAuthError("Sign-in state did not match; the callback was rejected.")
        if received.get("error"):
            raise ChatGPTAuthError("ChatGPT sign-in was declined or could not complete.")
        code = received.get("code")
        issued_id = received.get("client_id") or (prior and prior["client_id"])
        if not code or not issued_id or issued_id == "dynamic_agent_client":
            raise ChatGPTAuthError("ChatGPT registration did not return a code and issued client ID.")
        if prior and issued_id != prior["client_id"]:
            raise ChatGPTAuthError("ChatGPT returned a different client registration; sign-in was rejected.")
        response = requests.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "client_id": issued_id,
            "code": code, "code_verifier": verifier,
            "redirect_uri": redirect_uri, "resource": RESOURCE,
        }, timeout=30)
        if not response.ok:
            raise ChatGPTAuthError(f"ChatGPT code exchange failed (HTTP {response.status_code}).")
        token = response.json()
        id_token = token.get("id_token")
        if not id_token:
            raise ChatGPTAuthError("ChatGPT did not return an ID token.")
        signing_key = jwt.PyJWKClient(JWKS_URL).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(id_token, signing_key.key, algorithms=["RS256"],
                            audience=issued_id, issuer=ISSUER,
                            options={"require": ["exp", "iss", "aud", "sub", "nonce"]})
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ChatGPTAuthError("ChatGPT ID token nonce did not match.")
        if prior and (claims.get("sub") != prior["subject"] or issued_id != prior["client_id"]):
            raise ChatGPTAuthError("The signed-in ChatGPT account differs from the saved account.")
        scopes = token.get("scope", "").split()
        if not {"chatgpt.tokens.use.direct", "resource.invoke", "offline_access"}.issubset(scopes):
            raise ChatGPTAuthError("ChatGPT plan usage was not granted. Review the browser consent and app limit.")
        if not all(token.get(k) for k in ("access_token", "refresh_token")):
            raise ChatGPTAuthError("ChatGPT did not return renewable credentials.")
        now = time.time()
        record = {"email": claims.get("email", ""), "issuer": ISSUER,
                  "subject": claims["sub"], "client_id": issued_id,
                  "ext_agent_host_id": host_id, "id_token": id_token,
                  "access_token": token["access_token"],
                  "refresh_token": token["refresh_token"],
                  "token_type": token.get("token_type", "Bearer"),
                  "expires_at": now + int(token.get("expires_in", 3600)),
                  "scopes": scopes, "saved_at": now}
        with self._locked():
            data = self._read()
            data.setdefault("accounts", {})[issued_id] = record
            data["active_client_id"] = issued_id
            self._write(data)
        return {"email": record["email"], "client_id": issued_id}

    def get_access_token(self):
        with self._locked():
            data = self._read()
            active = data.get("active_client_id")
            record = data.get("accounts", {}).get(active) if active else None
            if not record:
                raise ChatGPTAuthError("Connect with ChatGPT before using this provider.")
            if "chatgpt.tokens.use.direct" not in record.get("scopes", []):
                raise ChatGPTAuthError("This account has not granted ChatGPT plan usage.")
            if time.time() < record.get("expires_at", 0) - 120:
                return record["access_token"]
            response = requests.post(TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": active,
                "refresh_token": record["refresh_token"], "resource": RESOURCE,
            }, timeout=30)
            if not response.ok:
                raise ChatGPTAuthError(f"ChatGPT session refresh failed (HTTP {response.status_code}); reconnect.")
            token = response.json()
            if not token.get("access_token") or not token.get("refresh_token"):
                raise ChatGPTAuthError("ChatGPT returned an incomplete refresh response; reconnect.")
            scopes = token.get("scope", "").split() or record["scopes"]
            if "chatgpt.tokens.use.direct" not in scopes:
                raise ChatGPTAuthError("ChatGPT plan usage is no longer granted; reconnect.")
            record.update({"access_token": token["access_token"],
                           "refresh_token": token["refresh_token"],
                           "expires_at": time.time() + int(token.get("expires_in", 3600)),
                           "scopes": scopes, "saved_at": time.time()})
            data["accounts"][active] = record
            self._write(data)
            return record["access_token"]


def main():
    parser = argparse.ArgumentParser(description="Connect Voices of History to your ChatGPT plan")
    parser.add_argument("command", choices=("login", "status", "models"))
    parser.add_argument("--timeout", type=int, default=600,
                        help="Seconds to allow for browser sign-in (default: 600)")
    args = parser.parse_args()
    auth = ChatGPTAuth()
    try:
        if args.command == "login":
            result = auth.login(timeout=args.timeout)
            print("Connected ChatGPT account:", result["email"] or "(email unavailable)")
        elif args.command == "status":
            status = auth.status()
            print("Connected:", status["connected"])
            if status["connected"]:
                print("Account:", status["email"] or "(email unavailable)")
                print("Plan usage granted:", status["plan_enabled"])
        else:
            token = auth.get_access_token()
            response = requests.get(RESOURCE + "/models",
                                    headers={"Authorization": "Bearer " + token}, timeout=30)
            if not response.ok:
                raise ChatGPTAuthError(f"Model list failed (HTTP {response.status_code}).")
            for model in response.json().get("models", []):
                if model.get("visibility") == "list":
                    print(model.get("slug"), "-", model.get("display_name"))
    except (ChatGPTAuthError, requests.RequestException, jwt.PyJWTError) as exc:
        print("Connection error:", exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
