"""Token vault: value -> placeholder, placeholder -> value. SQLite, file mode 0600.

Placeholders:
  known person (dictionary):  [P12_NAME] [P12_EMAIL] [P12_PHONE] [P12_ID]
  unknown (rule hit):         [EMAIL_7] [PHONE_3] [NAME_12] ...
Tokens are never reused for a different value, and a value always maps to the
same token on this machine, so the model's reasoning stays coherent across
sessions and sources.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

TOKEN_RE = re.compile(r"\[(?:P\d+_[A-Z_]+|[A-Z_]+_\d+)\]")


class Vault:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        # value -> token is unique; several spellings of one person's name share
        # one person-scoped token, so `token` is indexed but not unique.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tokens (value TEXT PRIMARY KEY, token TEXT NOT NULL, kind TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS tokens_token ON tokens (token)")
        self._db.execute("CREATE TABLE IF NOT EXISTS counters (kind TEXT PRIMARY KEY, n INTEGER NOT NULL)")
        self._db.commit()
        if not existed:
            os.chmod(self.path, 0o600)

    def token_for(self, kind: str, value: str, person: str | None = None) -> str:
        with self._lock:
            row = self._db.execute("SELECT token FROM tokens WHERE value = ?", (value,)).fetchone()
            if row:
                return row[0]
            if person:
                # every spelling of this person's name/email/... shares one token
                token = f"[{person}_{kind}]"
            else:
                cur = self._db.execute("SELECT n FROM counters WHERE kind = ?", (kind,)).fetchone()
                n = (cur[0] if cur else 0) + 1
                self._db.execute("INSERT OR REPLACE INTO counters (kind, n) VALUES (?, ?)", (kind, n))
                token = f"[{kind}_{n}]"
            self._db.execute("INSERT INTO tokens (value, token, kind) VALUES (?, ?, ?)", (value, token, kind))
            self._db.commit()
            return token

    def value_for(self, token: str) -> str | None:
        # the first spelling recorded for a person-scoped token is its display form
        row = self._db.execute("SELECT value FROM tokens WHERE token = ? ORDER BY rowid LIMIT 1", (token,)).fetchone()
        return row[0] if row else None

    def unmask(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self.value_for(m.group(0)) or m.group(0), text)

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM tokens").fetchone()[0]
