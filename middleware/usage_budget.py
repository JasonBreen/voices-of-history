"""Persistent request allowances. This is NOT the account's remaining plan quota."""
from contextlib import closing
from pathlib import Path
import json
import math
import sqlite3
import threading
import time
import uuid


DEFAULTS = {
    "calls_per_day": 30, "tokens_per_day": 100000,
    "automatic_calls_per_day": 6, "automatic_tokens_per_day": 20000,
    "automatic_calls_per_ten_minutes": 3,
    "max_output_tokens": 1200, "automatic_max_output_tokens": 800,
    "max_input_chars": 30000,
}


class BudgetExceeded(RuntimeError):
    pass


class UsageBudget:
    def __init__(self, path, settings=None, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = {**DEFAULTS, **(settings or {})}
        for key in DEFAULTS:
            value = self.settings[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("Usage limits must be positive whole numbers: " + key)
        self.clock = clock
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, started REAL NOT NULL, purpose TEXT NOT NULL,
                    profile TEXT, reserved INTEGER NOT NULL, charged INTEGER NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, usage_json TEXT,
                    state TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_time ON requests(started);
                CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value TEXT);
            """)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def reserve(self, messages, purpose="chat", profile="vanilla"):
        automatic = purpose == "automatic"
        # No token-counting model request. Estimate locally and label it as such.
        chars = sum(len(str(m.get("content", ""))) for m in messages)
        if chars > self.settings["max_input_chars"]:
            raise BudgetExceeded("This request is too large. Shorten the message or open a fresh conversation.")
        output = self.settings["automatic_max_output_tokens" if automatic else "max_output_tokens"]
        reserve = math.ceil(chars / 3) + output + 256
        now = self.clock()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            pause = db.execute("SELECT value FROM controls WHERE key='pause_until'").fetchone()
            if pause and float(pause[0]) > now:
                raise BudgetExceeded("Requests are paused after a provider quota/error response. No automatic retry will run.")
            rows = db.execute("SELECT started,purpose,charged FROM requests WHERE started>?",
                              (now - 86400,)).fetchall()
            if len(rows) >= self.settings["calls_per_day"]:
                raise BudgetExceeded("The shared daily Voices request allowance is used up.")
            if sum(r[2] for r in rows) + reserve > self.settings["tokens_per_day"]:
                raise BudgetExceeded("The shared daily Voices token allowance is used up.")
            if automatic:
                auto = [r for r in rows if r[1] == "automatic"]
                if len(auto) >= self.settings["automatic_calls_per_day"]:
                    raise BudgetExceeded("Automatic reviews are paused: daily review allowance reached.")
                if sum(r[2] for r in auto) + reserve > self.settings["automatic_tokens_per_day"]:
                    raise BudgetExceeded("Automatic reviews are paused: daily token allowance reached.")
                if sum(r[0] > now - 600 for r in auto) >= self.settings["automatic_calls_per_ten_minutes"]:
                    raise BudgetExceeded("Automatic reviews are paused: ten-minute allowance reached.")
            request_id = uuid.uuid4().hex
            db.execute("INSERT INTO requests(id,started,purpose,profile,reserved,charged,state) VALUES(?,?,?,?,?,?,?)",
                       (request_id, now, purpose, profile, reserve, reserve, "reserved"))
        return request_id

    def finish(self, request_id, usage=None, failed=False):
        usage = usage if isinstance(usage, dict) else {}
        def number(key, fallback):
            value = usage.get(key, usage.get(fallback))
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        inputs = number("input_tokens", "prompt_tokens")
        outputs = number("output_tokens", "completion_tokens")
        total = number("total_tokens", "total_tokens")
        if total is None and inputs is not None and outputs is not None:
            total = inputs + outputs
        with closing(self._connect()) as db, db:
            db.execute("UPDATE requests SET charged=COALESCE(?,reserved), input_tokens=?,output_tokens=?,"
                       "usage_json=?,state=? WHERE id=? AND state='reserved'",
                       (total, inputs, outputs, json.dumps(usage), "failed" if failed else "completed", request_id))

    def pause_provider(self):
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO controls VALUES('pause_until',?)", (str(self.clock() + 3600),))

    def snapshot(self):
        now = self.clock()
        with closing(self._connect()) as db:
            rows = db.execute("SELECT started,purpose,charged,input_tokens,state FROM requests WHERE started>?",
                              (now - 86400,)).fetchall()
            pause = db.execute("SELECT value FROM controls WHERE key='pause_until'").fetchone()
        auto = [r for r in rows if r[1] == "automatic"]
        return {"calls": len(rows), "tokens": sum(r[2] for r in rows),
                "automatic_calls": len(auto), "automatic_tokens": sum(r[2] for r in auto),
                "estimated_or_pending": sum(r[3] is None for r in rows),
                "provider_paused": bool(pause and float(pause[0]) > now),
                "next_request_expiry": min((r[0] + 86400 for r in rows), default=None),
                "limits": dict(self.settings)}


class BudgetClient:
    """Serialize requests and meter every provider invocation, including failed attempts."""
    def __init__(self, client, cfg):
        self.client, self.cfg = client, cfg
        self.lock = threading.Lock()
        path = cfg.get("usage", {}).get("ledger", str(Path(__file__).resolve().parent / "user-data/voices-usage.sqlite3"))
        settings = cfg.get("usage", {})
        if not cfg.get("unified") and "max_input_chars" not in settings:
            # The older upstream role prompts alone can exceed 40k characters.
            # Keep rollback profiles usable; the same daily token/request caps still apply.
            settings = {**settings, "max_input_chars": 60000}
        self.budget = UsageBudget(path, settings)

    def __getattr__(self, key):
        return getattr(self.client, key)

    def interactive_chat(self, messages):
        return self.chat(messages, purpose="chat")

    def chat(self, messages, purpose="automatic"):
        with self.lock:
            # Provider-specific instructions are also billed; include them in the estimate.
            from profiles import request_instructions
            instructions = request_instructions(self.cfg)
            estimate = list(messages) + [{"content": instructions}]
            schema = getattr(self.client, "response_schema", None)
            if isinstance(schema, dict):
                estimate.append({"content": json.dumps(schema, separators=(",", ":"))})
            request_id = self.budget.reserve(estimate, purpose, self.cfg.get("game_profile", "vanilla"))
            self.client.last_usage = None
            limits = self.budget.settings
            self.client.output_limit = limits["automatic_max_output_tokens" if purpose == "automatic" else "max_output_tokens"]
            try:
                result = self.client.chat(messages)
            except Exception as error:
                self.budget.finish(request_id, getattr(self.client, "last_usage", None), failed=True)
                if any(code in str(error) for code in ("429", "402", "quota", "rate_limit")):
                    self.budget.pause_provider()
                raise
            self.budget.finish(request_id, getattr(self.client, "last_usage", None))
            return result
