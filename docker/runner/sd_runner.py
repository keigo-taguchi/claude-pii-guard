"""In-container runner for safe-data py_run.

Provides the `sd` module to the user script:
    df = sd.load("<file id>")      -> pandas DataFrame (PII columns are tainted)
    sd.result(obj)                  -> the only thing that leaves the container
    sd.log("msg")                   -> up to 20 short lines, also checked

Writes exactly one JSON line to stdout at the end. The user script's own
stdout/stderr are captured and discarded.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import re
import runpy
import sys
import traceback
import types
import unicodedata
from pathlib import Path

CFG = json.loads(os.environ.get("SD_CONFIG", "{}"))
PII_PATTERNS = [p.lower() for p in CFG.get("pii_patterns", [])]
RESULT_BYTES = int(CFG.get("py_result_bytes", 8192))
STRING_MAX = int(CFG.get("py_string_max", 64))
DATA = Path("/data")

_WS = re.compile(r"[\s　]+")
_DIG = re.compile(r"\d")


def _kata(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def _forms(v: str) -> set[str]:
    v = v.strip()
    if not v:
        return set()
    b = unicodedata.normalize("NFKC", v)
    out = set()
    for f in (v, b, b.lower(), _kata(b), _kata(b).lower()):
        out.add(f)
        out.add(_WS.sub("", f))
    d = "".join(_DIG.findall(b))
    if len(d) >= 7:
        out.add(d)
    return {f for f in out if f}


def _is_pii_col(name: str) -> bool:
    n = str(name).lower()
    nc = re.sub(r"[\s_\-]", "", n)
    return any(p in n or re.sub(r"[\s_\-]", "", p) in nc for p in PII_PATTERNS)


class _SD(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("sd")
        self._taint: set[str] = set()
        self._result = None
        self._has_result = False
        self.logs: list[str] = []

    def load(self, file_id: str, **kw):
        import pandas as pd

        matches = list(DATA.glob(f"{file_id}.*")) if DATA.exists() else []
        if not matches:
            raise FileNotFoundError(f"input {file_id} was not mounted; pass it in py_run inputs")
        p = matches[0]
        ext = p.suffix.lower()
        if ext in (".csv", ".txt"):
            df = pd.read_csv(p, encoding=kw.pop("encoding", "utf-8-sig"), **kw)
        elif ext == ".tsv":
            df = pd.read_csv(p, sep="\t", encoding=kw.pop("encoding", "utf-8-sig"), **kw)
        elif ext == ".parquet":
            df = pd.read_parquet(p, **kw)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(p, **kw)
        elif ext in (".json", ".jsonl"):
            df = pd.read_json(p, lines=ext == ".jsonl", **kw)
        else:
            raise ValueError(f"unsupported input type {ext}")
        for col in df.columns:
            if _is_pii_col(col):
                for v in df[col].dropna().astype(str).unique():
                    for f in _forms(v):
                        if (f.isdigit() and len(f) >= 7) or (not f.isdigit() and len(f) >= 3):
                            self._taint.add(f)
        return df

    def result(self, obj) -> None:
        self._result = obj
        self._has_result = True

    def log(self, msg) -> None:
        if len(self.logs) < 20:
            self.logs.append(str(msg)[:200])


def _plain(o, depth=0):
    """Convert pandas/numpy/sets/dates to JSON-safe, with string truncation."""
    if depth > 6:
        return "[nested too deep]"
    try:
        import numpy as np
        import pandas as pd
    except Exception:  # pragma: no cover
        np = pd = None  # type: ignore
    if pd is not None and isinstance(o, pd.DataFrame):
        return _plain(o.to_dict(orient="records"), depth + 1)
    if pd is not None and isinstance(o, pd.Series):
        return _plain(o.to_dict(), depth + 1)
    if np is not None and isinstance(o, np.generic):
        return _plain(o.item(), depth + 1)
    if isinstance(o, dict):
        return {str(k)[:STRING_MAX]: _plain(v, depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_plain(v, depth + 1) for v in o]
    if isinstance(o, float):
        return None if math.isnan(o) or math.isinf(o) else o
    if isinstance(o, (int, bool)) or o is None:
        return o
    if isinstance(o, bytes):
        return "[bytes withheld]"
    s = str(o)
    return s if len(s) <= STRING_MAX else s[:STRING_MAX] + "…"


def _check(text: str, taint: set[str]) -> int:
    b = unicodedata.normalize("NFKC", text)
    k = _kata(b)
    hay = [text, b, b.lower(), _WS.sub("", b), k, k.lower(), _WS.sub("", k), "".join(_DIG.findall(b))]
    return sum(1 for f in taint if any(f in h for h in hay))


def main() -> None:
    sd = _SD()
    sys.modules["sd"] = sd
    buf = io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            runpy.run_path("/work/script.py", run_name="__main__")
    except SystemExit:
        pass
    except Exception as e:  # noqa: BLE001
        tb = traceback.extract_tb(e.__traceback__)
        line = next((f.lineno for f in reversed(tb) if f.filename.endswith("script.py")), None)
        error = {"type": type(e).__name__, "message": str(e)[:200], "script_line": line}

    out: dict
    if error:
        hits = _check(json.dumps(error, ensure_ascii=False), sd._taint)
        if hits:
            error["message"] = "[withheld: exception text contained protected values]"
        out = {"ok": False, "error": f"script raised {error['type']}", "reason": error}
    elif not sd._has_result:
        out = {"ok": False, "error": "script did not call sd.result(...)"}
    else:
        res = _plain(sd._result)
        text = json.dumps(res, ensure_ascii=False)
        if len(text.encode("utf-8")) > RESULT_BYTES:
            out = {"ok": False, "error": f"result is {len(text.encode('utf-8'))} bytes; limit {RESULT_BYTES}",
                   "reason": {"kind": "too_large", "hint": "return aggregates, not rows"}}
        else:
            hits = _check(text + "\n" + "\n".join(sd.logs), sd._taint)
            if hits:
                out = {"ok": False, "error": "result contains protected values",
                       "reason": {"kind": "egress", "protected_values_matched": hits,
                                  "hint": "do not return rows or raw cells from PII columns; aggregate, count, or bucket instead"}}
            else:
                out = {"ok": True, "result": res, "logs": sd.logs}
    sys.__stdout__.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


if __name__ == "__main__":
    main()
