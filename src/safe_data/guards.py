"""Guards shared by the safe-data tools.

Nothing in this module returns a protected value. Reasons describe *what kind*
of thing was found and *how many*, never the value itself.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

# ---------------------------------------------------------------- SQL guard

_FORBIDDEN_WORDS = [
    # writes / DDL / control
    "insert", "update", "delete", "merge", "drop", "alter", "create", "grant", "revoke",
    "truncate", "call", "do", "execute", "lock", "vacuum", "analyze", "listen", "notify",
    "copy", "set", "reset", "show", "begin", "commit", "rollback", "savepoint", "prepare",
    "deallocate", "discard", "refresh", "cluster", "reindex", "comment", "security",
    # introspection / escape hatches
    "current_setting", "pg_settings", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "dblink", "pg_sleep", "lo_import", "lo_export", "lo_get", "pg_catalog",
    "information_schema", "search_path", "pg_shadow", "pg_authid", "pg_user", "pg_roles",
    "set_config", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_file_write", "pg_logdir_ls", "pg_stat_activity",
]
_FORBIDDEN_RE = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, _FORBIDDEN_WORDS)) + r")(?![\w])", re.I)
_SELECT_STAR_RE = re.compile(r"\bselect\s+(?:distinct\s+)?(?:all\s+)?\*|[,(]\s*\*\s*(?=[,)]|from\b)|\w+\.\*", re.I)
_LIMIT_RE = re.compile(r"\blimit\b", re.I)


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def check_sql(sql: str) -> str | None:
    """Return a rejection reason, or None when the statement is acceptable.

    Accepts a single SELECT / WITH ... SELECT. Rejects anything that writes,
    changes session state, reads files, or selects every column.
    """
    s = strip_sql_comments(sql).strip().rstrip(";").strip()
    if not s:
        return "empty statement"
    if ";" in s:
        return "only one statement per call"
    if "\x00" in s:
        return "null byte in statement"
    head = s.split(None, 1)[0].lower()
    if head not in ("select", "with", "values", "table", "explain"):
        return f"statement must start with SELECT or WITH (got {head.upper()})"
    if head in ("values", "table", "explain"):
        return f"{head.upper()} is not allowed; use SELECT"
    m = _FORBIDDEN_RE.search(s)
    if m:
        return f"forbidden keyword: {m.group(1).upper()}"
    # count(*) / count(DISTINCT x) are aggregates, not column wildcards
    s_no_count = re.sub(r"\bcount\s*\(\s*\*\s*\)", "count(1)", s, flags=re.I)
    if _SELECT_STAR_RE.search(s_no_count):
        return "SELECT * is not allowed; name the columns you need (schema_describe lists them)"
    if "$$" in s or "$body$" in s.lower():
        return "dollar-quoted blocks are not allowed"
    return None


def wrap_limit(sql: str, max_rows: int) -> str:
    """Wrap the user's query so the row count is bounded server-side."""
    inner = strip_sql_comments(sql).strip().rstrip(";").strip()
    return f"SELECT * FROM (\n{inner}\n) AS _safe_q LIMIT {int(max_rows)}"


# ------------------------------------------------------ small-cell suppression

_COUNT_COL_RE = re.compile(
    r"^(n|cnt|count|num|number|total|rows|件数|人数|回数|users?|patients?|members?|tickets?|events?|sessions?)([_\s].*)?$",
    re.I,
)


def is_count_column(name: str) -> bool:
    return bool(_COUNT_COL_RE.match(name.strip())) or name.strip().lower().startswith("count(")


def suppress_small_cells(columns: list[str], rows: list[list[Any]], threshold: int) -> tuple[list[list[Any]], int]:
    """Null out count-like integer cells with 0 < value < threshold.

    This is a best-effort statistical disclosure control for aggregate queries:
    a group with 3 people must not reveal that there *are* 3 people. Columns are
    identified by name, so views should name their counts clearly.
    """
    idx = [i for i, c in enumerate(columns) if is_count_column(c)]
    if not idx:
        return rows, 0
    suppressed = 0
    out: list[list[Any]] = []
    for row in rows:
        row = list(row)
        for i in idx:
            v = row[i]
            if isinstance(v, bool):
                continue
            if isinstance(v, int) and 0 < v < threshold:
                row[i] = None
                suppressed += 1
        out.append(row)
    return out, suppressed


# ------------------------------------------------------------- egress guard

_KANA_SHIFT = 0x60
_DIGITS_RE = re.compile(r"\d")
_WS_RE = re.compile(r"[\s　]+")


def _hira_to_kata(s: str) -> str:
    return "".join(chr(ord(ch) + _KANA_SHIFT) if "ぁ" <= ch <= "ゖ" else ch for ch in s)


def normalize_forms(value: str) -> set[str]:
    """All the shapes a protected value could take after light transformation.

    raw, NFKC, whitespace-stripped, hiragana folded to katakana, digits only
    (for numeric identifiers), lowercase. Used on both the protected values and
    the text under inspection, so a match survives the transformations a script
    or model is likely to apply.
    """
    forms: set[str] = set()
    v = value.strip()
    if not v:
        return forms
    base = unicodedata.normalize("NFKC", v)
    for f in (v, base, base.lower(), _hira_to_kata(base), _hira_to_kata(base).lower()):
        forms.add(f)
        forms.add(_WS_RE.sub("", f))
    digits = "".join(_DIGITS_RE.findall(base))
    if len(digits) >= 7:
        forms.add(digits)
    return {f for f in forms if f}


class EgressGuard:
    """Rejects text that contains any protected value in any normalized form."""

    def __init__(self, values: Iterable[Any], min_len: int = 3, min_digits: int = 7) -> None:
        self.min_len = min_len
        self.min_digits = min_digits
        self._forms: set[str] = set()
        for v in values:
            if v is None:
                continue
            if isinstance(v, float) and math.isnan(v):
                continue
            s = str(v)
            if not s.strip():
                continue
            for f in normalize_forms(s):
                if f.isdigit():
                    if len(f) >= min_digits:
                        self._forms.add(f)
                elif len(f) >= min_len:
                    self._forms.add(f)

    def __len__(self) -> int:
        return len(self._forms)

    def check(self, text: str) -> list[dict[str, Any]]:
        """Return a list of {type, count} hits. Never returns the matched values."""
        if not self._forms or not text:
            return []
        haystacks = _haystacks(text)
        count = 0
        for f in self._forms:
            if any(f in h for h in haystacks):
                count += 1
        return [{"type": "protected_value", "count": count}] if count else []


def _haystacks(text: str) -> list[str]:
    base = unicodedata.normalize("NFKC", text)
    kata = _hira_to_kata(base)
    return [
        text,
        base,
        base.lower(),
        _WS_RE.sub("", base),
        kata,
        kata.lower(),
        _WS_RE.sub("", kata),
        "".join(_DIGITS_RE.findall(base)),
    ]


# ------------------------------------------------------ encoded blob gate

# base64 alphabet is a superset of hex, so one pattern covers both encodings
_B64_RUN = re.compile(r"[A-Za-z0-9+/=_-]{48,}")


def encoded_blob_hits(text: str) -> int:
    """Count long base64/hex runs that could smuggle data past value matching."""
    return len(_B64_RUN.findall(text))


def withhold_encoded_blobs(text: str) -> tuple[str, int]:
    n = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return "[ENCODED_BLOB_WITHHELD]"

    text = _B64_RUN.sub(_sub, text)
    return text, n


# -------------------------------------------------------------- output cap

def cap_output(obj: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Shrink a result dict until its JSON form fits under max_chars.

    Row lists are truncated first; if still too large, a summary replaces the
    payload. The 'truncated' flag tells Claude what happened.
    """
    def size(o: Any) -> int:
        return len(json.dumps(o, ensure_ascii=False, default=str))

    if size(obj) <= max_chars:
        return obj
    out = dict(obj)
    out["truncated"] = True
    for key in ("rows", "sample", "files", "items", "result", "logs"):
        if key in out and isinstance(out[key], list):
            rows = out[key]
            lo, hi = 0, len(rows)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                out[key] = rows[:mid]
                if size(out) <= max_chars:
                    lo = mid
                else:
                    hi = mid - 1
            out[key] = rows[:lo]
            out[f"{key}_dropped"] = len(rows) - lo
            if size(out) <= max_chars:
                return out
    return {
        "ok": out.get("ok", True),
        "truncated": True,
        "note": f"result exceeded {max_chars} characters and was withheld; narrow the query",
    }
