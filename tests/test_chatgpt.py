from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "middleware"))

from chatgpt_auth import ChatGPTAuth, ChatGPTAuthError, _protect, _unprotect
from chatgpt_client import ChatGPTPlanClient
from injector import Injector
from main import App


class FakeResponse:
    def __init__(self, data=None, lines=(), status=200):
        self.data = data or {}
        self.lines = lines
        self.status_code = status
        self.ok = status < 400

    def json(self):
        return self.data

    def iter_lines(self, decode_unicode=False):
        return iter(self.lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ChatGPTTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_can_be_read_by_same_user(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = ChatGPTAuth(folder)
            with auth._locked():
                auth._write({"host_id": "urn:uuid:test", "accounts": {},
                             "active_client_id": None})
            raw = auth.credential_file.read_bytes()
            self.assertNotIn(b"urn:uuid:test", raw)
            self.assertEqual(_unprotect(raw),
                             b'{"host_id":"urn:uuid:test","accounts":{},"active_client_id":null}')
            self.assertEqual(auth._read()["host_id"], "urn:uuid:test")

    def test_login_uses_dynamic_registration_and_validates_callback(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = ChatGPTAuth(folder)
            captured = {}

            def browser_open(url):
                params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
                captured.update(params)
                def callback():
                    import requests
                    requests.get(params["redirect_uri"] + "?" + urlencode({
                        "state": params["state"], "code": "fake-code",
                        "client_id": "oaiapp_test"}), timeout=5)
                threading.Thread(target=callback, daemon=True).start()
                return True

            class FakeJWK:
                def get_signing_key_from_jwt(self, _token):
                    return type("Key", (), {"key": key.public_key()})()

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            def token_exchange(_url, **_kwargs):
                id_token = jwt.encode({"iss": "https://auth.openai.com",
                    "aud": "oaiapp_test", "sub": "account-1",
                    "email": "test@example.invalid", "nonce": captured["nonce"],
                    "exp": int(time.time()) + 3600}, key, algorithm="RS256")
                return FakeResponse({"id_token": id_token,
                    "access_token": "dummy-access", "refresh_token": "dummy-refresh",
                    "expires_in": 3600,
                    "scope": "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"})
            with patch("chatgpt_auth.webbrowser.open", side_effect=browser_open), \
                 patch("chatgpt_auth.requests.post", side_effect=token_exchange), \
                 patch("chatgpt_auth.jwt.PyJWKClient", return_value=FakeJWK()):
                result = auth.login(timeout=5)
            self.assertEqual(captured["client_id"], "dynamic_agent_client")
            self.assertEqual(captured["agent_name_hint"], "Voices of History")
            self.assertEqual(result["client_id"], "oaiapp_test")
            self.assertTrue(auth.status()["plan_enabled"])
            self.assertNotIn(b"dummy-access", auth.credential_file.read_bytes())

    def test_refresh_replaces_both_rotating_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            auth = ChatGPTAuth(folder)
            with auth._locked():
                auth._write({"host_id": "urn:uuid:test", "active_client_id": "oaiapp_test",
                             "accounts": {"oaiapp_test": {
                                 "client_id": "oaiapp_test", "access_token": "old-access",
                                 "refresh_token": "old-refresh", "expires_at": time.time()-1,
                                 "scopes": ["chatgpt.tokens.use.direct"]}}})
            new = {"access_token": "new-access", "refresh_token": "new-refresh",
                   "expires_in": 3600,
                   "scope": "offline_access resource.invoke chatgpt.tokens.use.direct"}
            with patch("chatgpt_auth.requests.post", return_value=FakeResponse(new)) as request:
                self.assertEqual(auth.get_access_token(), "new-access")
            self.assertEqual(request.call_args.kwargs["data"]["refresh_token"], "old-refresh")
            saved = auth._read()["accounts"]["oaiapp_test"]
            self.assertEqual(saved["refresh_token"], "new-refresh")

    def test_responses_request_requires_completed_event_and_safe_fields(self):
        client = ChatGPTPlanClient({"chatgpt_plan": {"model": "gpt-test"}})
        client.auth = type("Auth", (), {"get_access_token": lambda self: "fake-token"})()
        lines = [b'data: {"type":"response.output_text.delta","delta":"Hello"}',
                 b'data: {"type":"response.completed","response":{}}', b'data: [DONE]']
        with patch("chatgpt_client.requests.post", return_value=FakeResponse(lines=lines)) as request:
            result = client.chat([{"role": "system", "content": "Answer briefly"},
                                  {"role": "user", "content": "Hi"}])
        self.assertEqual(result, "Hello")
        body = request.call_args.kwargs["json"]
        self.assertEqual(body["instructions"], "Answer briefly")
        self.assertEqual(body["input"], [{"role": "user", "content": "Hi"}])
        self.assertEqual((body["store"], body["stream"]), (False, True))
        self.assertNotIn("temperature", body)
        with patch("chatgpt_client.requests.post", return_value=FakeResponse(lines=lines[:1])):
            with self.assertRaisesRegex(ChatGPTAuthError, "response.completed"):
                client.chat([{"role": "user", "content": "Hi"}])

    def test_dry_run_never_writes_command_or_localisation_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            loc = root / "localisation.yml"
            loc.write_text('l_english:\n aidiplo.1.t:0 "Old"\n', encoding="utf-8")
            cfg = {"paths": {"game_log": str(root / "logs" / "game.log"),
                             "mod_localisation": str(loc)},
                   "injection": {"dry_run": True}}
            notes = []
            injector = Injector(cfg, logger=notes.append)
            self.assertEqual(injector.write_run_file(["pp 500"]), 1)
            injector._set_loc_keys({"aidiplo.1.t": "New"})
            self.assertFalse((root / "aidiplo.txt").exists())
            self.assertEqual(loc.read_text(encoding="utf-8"),
                             'l_english:\n aidiplo.1.t:0 "Old"\n')
            self.assertIn("DRY RUN", " ".join(notes))

    def test_unified_requests_require_structured_actions_without_changing_legacy_requests(self):
        lines = [b'data: {"type":"response.completed","response":{}}']
        for unified in (False, True):
            client = ChatGPTPlanClient({"chatgpt_plan": {"model": "gpt-6.1-sol"},
                                       "unified": unified, "game_profile": "the_fire_rises"})
            client.auth = type("Auth", (), {"get_access_token": lambda self: "fake-token"})()
            with patch("chatgpt_client.requests.post", return_value=FakeResponse(lines=lines)) as request:
                client.chat([{"role": "user", "content": "Discuss goodwill."}])
            body = request.call_args.kwargs["json"]
            self.assertFalse(body["store"])
            if unified:
                output = body["text"]["format"]
                self.assertEqual(output["type"], "json_schema")
                self.assertTrue(output["strict"])
                schema = output["schema"]
                self.assertEqual(set(schema["required"]), {"message", "decision", "actions"})
                self.assertFalse(schema["additionalProperties"])
                choices = schema["properties"]["actions"]["items"]["anyOf"]
                names = {name for choice in choices for name in choice["properties"]["name"]["enum"]}
                self.assertIn("improve_relations", names)
                self.assertNotIn("raw_console", names)
                self.assertNotIn("declare_war", names)
                self.assertEqual(body["reasoning"], {"effort": "low"})
            else:
                self.assertNotIn("text", body)

    def test_dispatch_returns_immediately_in_dry_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cfg = {"paths": {"game_log": str(root / "logs" / "game.log"),
                             "mod_localisation": str(root / "localisation.yml")},
                   "injection": {"dry_run": True}}
            app = App.__new__(App)
            app.injector = Injector(cfg, logger=lambda _note: None)
            app._inject_lock = threading.Lock()
            app.q = queue.Queue()
            app.running = True
            app._dispatch_run(["pp 500"])
            self.assertEqual(app.q.get_nowait()[0], "action")
            self.assertFalse((root / "aidiplo.txt").exists())


if __name__ == "__main__":
    unittest.main()
