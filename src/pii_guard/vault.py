"""Token vault: value -> placeholder, placeholder -> value. SQLite, encrypted at rest.

Placeholders:
  known person (dictionary):  [P12_NAME] [P12_EMAIL] [P12_PHONE] [P12_ID]
  unknown (rule hit):         [EMAIL_7] [PHONE_3] [NAME_12] ...
A value always maps to the same token on this machine; every spelling of one
person's name shares that person's token. Tokens are never reused.

At rest the value is stored as Fernet ciphertext; lookups use an HMAC of the
value, so the database file reveals neither values nor their shapes. The key
lives in ~/.config/safe-data/vault.key (0600), generated on first use.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from pathlib import Path

from cryptography.fernet import Fernet

TOKEN_RE = re.compile(r"\[(?:P\d+_[A-Z_]+|[A-Z_]+_\d+)\]")


def _load_or_create_key(path: Path) -> bytes:
    path = Path(path).expanduser()
    if path.exists():
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(f"vault key {path} must be mode 0600 (is {oct(mode)})")
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = base64.urlsafe_b64encode(secrets.token_bytes(32))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key + b"\n")
    return key


class Vault:
    def __init__(self, path: Path, key_path: Path | None = None) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = _load_or_create_key(key_path or self.path.with_name("vault.key"))
        self._fernet = Fernet(key)
        self._mac_key = hashlib.sha256(b"lookup:" + key).digest()
        existed = self.path.exists()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        # value_mac -> token is unique; several spellings of one person's name
        # share one person-scoped token, so `token` is indexed but not unique.
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tokens (value_mac TEXT PRIMARY KEY, token TEXT NOT NULL, kind TEXT NOT NULL, value_enc BLOB NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS tokens_token ON tokens (token)")
        self._db.execute("CREATE TABLE IF NOT EXISTS counters (kind TEXT PRIMARY KEY, n INTEGER NOT NULL)")
        self._db.commit()
        if not existed:
            os.chmod(self.path, 0o600)

    def _mac(self, value: str) -> str:
        return hmac.new(self._mac_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def token_for(self, kind: str, value: str, person: str | None = None) -> str:
        mac = self._mac(value)
        with self._lock:
            row = self._db.execute("SELECT token FROM tokens WHERE value_mac = ?", (mac,)).fetchone()
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
            enc = self._fernet.encrypt(value.encode("utf-8"))
            self._db.execute("INSERT INTO tokens (value_mac, token, kind, value_enc) VALUES (?, ?, ?, ?)", (mac, token, kind, enc))
            self._db.commit()
            return token

    def value_for(self, token: str) -> str | None:
        # the first spelling recorded for a person-scoped token is its display form
        row = self._db.execute("SELECT value_enc FROM tokens WHERE token = ? ORDER BY rowid LIMIT 1", (token,)).fetchone()
        if not row:
            return None
        return self._fernet.decrypt(bytes(row[0])).decode("utf-8")

    def unmask(self, text: str) -> str:
        return TOKEN_RE.sub(lambda m: self.value_for(m.group(0)) or m.group(0), text)

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM tokens").fetchone()[0]
