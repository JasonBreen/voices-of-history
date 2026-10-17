"""A small country pilot driven by completed native daily exports, never wall-clock polling."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3


class Strategist:
    def __init__(self, profile, settings_path, ledger):
        self.profile, self.path, self.ledger = profile, Path(settings_path), Path(ledger)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.settings = {"enabled": False, "countries": []}
        if self.path.exists():
            self.settings.update(json.loads(self.path.read_text(encoding="utf-8")))
        countries = self.settings.get("countries")
        if (not isinstance(countries, list) or len(countries) > 3 or
                any(not isinstance(tag, str) for tag in countries) or len(set(countries)) != len(countries)):
            raise ValueError("Invalid saved AI pilot country selection.")
        if self.settings["enabled"] and len(countries) != 3:
            self.settings["enabled"] = False
        self.previous = None
        self.crises = []
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS baselines(profile TEXT,gid INTEGER,actor TEXT,day INTEGER,
                  PRIMARY KEY(profile,gid,actor));
                CREATE TABLE IF NOT EXISTS reviews(profile TEXT,gid INTEGER,actor TEXT,day INTEGER,
                  reason TEXT,signature TEXT,summary TEXT,status TEXT,
                  PRIMARY KEY(profile,gid,actor,signature));
            """)

    def connect(self):
        return sqlite3.connect(self.ledger, timeout=15)

    def save(self, countries, enabled, state, world):
        if len(countries) > 3 or len(set(countries)) != len(countries):
            raise ValueError("Choose at most three different AI major countries.")
        if enabled and len(countries) != 3:
            raise ValueError("Choose exactly three AI major countries for this pilot.")
        for tag in countries:
            if tag == state.get("tag") or world.get(tag, {}).get("maj") != "1":
                raise ValueError("Select synchronized major countries other than the player.")
        self.settings = {"enabled": bool(enabled), "countries": list(countries)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.settings, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        self.previous = (int(state.get("gid", 0)), int(state.get("day", 0)), state.get("tag"))
        self.crises.clear()
        # Start a new selection's weekly clock now; preserve completed reviews.
        with closing(self.connect()) as db, db:
            for tag in countries:
                db.execute("INSERT OR IGNORE INTO baselines VALUES(?,?,?,?)",
                           (self.profile, self.previous[0], tag, self.previous[1]))

    def crisis(self, text):
        if self.settings["enabled"] and text not in self.crises:
            self.crises = (self.crises + [text[:600]])[-3:]

    def due(self, state, world):
        """One candidate per new, contiguous daily tick. No bursts on startup/reload."""
        gid, day, player = int(state.get("gid", 0)), int(state.get("day", 0)), state.get("tag")
        current, previous = (gid, day, player), self.previous
        self.previous = current
        if not gid or not day:
            return None
        countries = self.settings["countries"]
        with closing(self.connect()) as db, db:
            for actor in countries:
                db.execute("INSERT OR IGNORE INTO baselines VALUES(?,?,?,?)", (self.profile, gid, actor, day))
            if not self.settings["enabled"] or previous is None or previous[0] != gid or previous[2] != player:
                self.crises.clear()
                return None
            if day <= previous[1] or day > previous[1] + 3:
                # Loading a save does not create a crisis or catch-up review backlog.
                if day != previous[1]:
                    self.crises.clear()
                    for actor in countries:
                        db.execute("UPDATE baselines SET day=? WHERE profile=? AND gid=? AND actor=?", (day, self.profile, gid, actor))
                return None
            candidates = []
            for actor in countries:
                nation = world.get(actor, {})
                if actor == player or nation.get("maj") != "1" or nation.get("cap") == "1" or nation.get("date") != state.get("date"):
                    continue
                start = db.execute("SELECT day FROM baselines WHERE profile=? AND gid=? AND actor=?", (self.profile, gid, actor)).fetchone()[0]
                last = db.execute("SELECT MAX(day) FROM reviews WHERE profile=? AND gid=? AND actor=? AND status!='budget_blocked'", (self.profile, gid, actor)).fetchone()[0]
                last = max(start, last or start)
                relevant = [c for c in self.crises if actor in c or nation.get("name", "\0") in c or player in c or state.get("name", "\0") in c]
                if relevant:
                    reason = "Crisis: " + " / ".join(relevant)
                    signature = "crisis:" + str(day // 7)
                elif day - last >= 7:
                    reason, signature = "Weekly strategic review", "week:" + str(day // 7)
                else:
                    continue
                exists = db.execute("SELECT status FROM reviews WHERE profile=? AND gid=? AND actor=? AND signature=?",
                                    (self.profile, gid, actor, signature)).fetchone()
                if exists and exists[0] != "budget_blocked":
                    continue
                candidates.append((last, actor, reason, signature))
            if not candidates:
                return None
            _, actor, reason, signature = sorted(candidates)[0]
            db.execute("INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,?,?)",
                       (self.profile, gid, actor, day, reason, signature, "", "pending"))
            return {"gid": gid, "day": day, "actor": actor, "reason": reason, "signature": signature}

    def finish(self, review, summary, status="preview"):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE reviews SET summary=?,status=? WHERE profile=? AND gid=? AND actor=? AND signature=?",
                       (summary[:4000], status, self.profile, review["gid"], review["actor"], review["signature"]))

    def history(self, gid, actor, day, limit=3):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT day,reason,summary,status FROM reviews WHERE profile=? AND gid=? AND actor=? AND day<=? AND summary!='' ORDER BY day DESC LIMIT ?",
                              (self.profile, gid, actor, day, limit)).fetchall()
        return [{"day": r[0], "reason": r[1], "summary": r[2][:900], "status": r[3]} for r in reversed(rows)]
