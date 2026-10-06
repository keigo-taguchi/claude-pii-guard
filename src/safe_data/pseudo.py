"""Pseudonymous identifiers shared by the DB views, the ETL, and the MCP server.

pseudo_id(key, "12345") == left(encode(hmac('12345', key, 'sha256'), 'hex'), 16)
in PostgreSQL (pgcrypto), so the same person gets the same pseudo ID across
the database, CSV exports, Sentry user IDs, and support tickets.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

PSEUDO_LEN = 16


def generate_key_file(path: Path) -> Path:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    return path


def load_key(path: Path) -> bytes:
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"pseudo key not found: {path} (run: safe-data-mcp --init-key)")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError(f"pseudo key {path} must be mode 0600 (is {oct(mode)})")
    return path.read_text(encoding="utf-8").strip().encode("utf-8")


def pseudo_id(key: bytes, value: object) -> str:
    """Deterministic, keyed, non-reversible identifier for a real ID."""
    digest = hmac.new(key, str(value).encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:PSEUDO_LEN]
