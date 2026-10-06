"""Describe files in the PII inbox without returning their contents."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:\+81|0)\d{1,4}[-\s()]?\d{1,4}[-\s()]?\d{3,4}")
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?\d*\.\d+(e[+-]?\d+)?$", re.I)
_SAMPLE_ROWS = 200
TEXT_EXT = {".csv", ".tsv", ".txt"}


def file_id(path: Path) -> str:
    return hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:8]


def list_inbox(cfg: Config) -> dict[str, Path]:
    inbox = cfg.pii_inbox
    if not inbox.exists():
        return {}
    files = sorted(p for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))
    return {file_id(p): p for p in files}


def _read_text(path: Path, limit_bytes: int = 2_000_000) -> str:
    raw = path.read_bytes()[:limit_bytes]
    for enc in ("utf-8-sig", "cp932", "euc_jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _infer_type(values: list[str]) -> str:
    vs = [v.strip() for v in values if v is not None and v.strip() != ""]
    if not vs:
        return "empty"
    if all(_INT_RE.match(v) for v in vs):
        return "int"
    if all(_INT_RE.match(v) or _FLOAT_RE.match(v) for v in vs):
        return "float"
    if all(_DATE_RE.match(v) for v in vs):
        return "date"
    if all(v.lower() in ("true", "false", "0", "1", "yes", "no") for v in vs):
        return "bool"
    return "text"


def _value_pii_signal(values: list[str]) -> str | None:
    vs = [v for v in values if v]
    if not vs:
        return None
    if sum(bool(_EMAIL_RE.search(v)) for v in vs) >= max(1, len(vs) // 2):
        return "email-like values"
    if sum(bool(_PHONE_RE.search(v)) for v in vs) >= max(1, len(vs) // 2):
        return "phone-like values"
    return None


def describe_file(cfg: Config, path: Path) -> dict[str, Any]:
    st = path.stat()
    info: dict[str, Any] = {
        "id": file_id(path),
        "ext": path.suffix.lower(),
        "bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
    }
    if path.suffix.lower() not in TEXT_EXT:
        info["note"] = "binary or unsupported format; load it in py_run (parquet/xlsx need pandas)"
        return info
    text = _read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:20_000], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel_tab if path.suffix.lower() == ".tsv" else csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        header = next(reader)
    except StopIteration:
        info["columns"] = []
        return info
    rows: list[list[str]] = []
    total = 0
    for row in reader:
        total += 1
        if len(rows) < _SAMPLE_ROWS:
            rows.append(row)
    columns = []
    for i, name in enumerate(header):
        col_vals = [r[i] if i < len(r) else "" for r in rows]
        dtype = _infer_type(col_vals)
        pii_by_name = cfg.is_pii_column(name)
        pii_by_value = _value_pii_signal(col_vals)
        columns.append({
            "name": name,
            "type": dtype,
            "pii": bool(pii_by_name or pii_by_value),
            "why": "column name" if pii_by_name else (pii_by_value or None),
            "null_ratio": round(sum(1 for v in col_vals if not v.strip()) / max(1, len(col_vals)), 2),
        })
    info["delimiter"] = "tab" if dialect.delimiter == "\t" else dialect.delimiter
    info["rows_estimate"] = total if total < _SAMPLE_ROWS else f">={total}"
    info["columns"] = columns
    return info
