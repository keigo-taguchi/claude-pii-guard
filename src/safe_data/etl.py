"""safeify-csv: turn a CSV that contains personal data into a safe TSV.

    safeify-csv ~/PII/inbox/users.csv --out ./data/safe/users.tsv \
        --pseudo user_id --year birth_date --drop note

Rules, in order:
  --pseudo COL   replace the value with pseudo_id(key, value)  (same key as the DB)
  --year COL     keep only the year of a date
  --keep COL     keep as-is even if the name looks like PII (use sparingly)
  --drop COL     drop
  default        drop every column whose name matches the PII patterns

The output never ends in .csv so that a Read deny on *.csv can stay broad.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

from .config import Config
from .files import _read_text
from .pseudo import load_key, pseudo_id

_YEAR_RE = re.compile(r"^(\d{4})[-/.]\d{1,2}[-/.]\d{1,2}")


def safeify(cfg: Config, src: Path, out: Path, pseudo: list[str], year: list[str], keep: list[str], drop: list[str]) -> dict:
    if out.suffix.lower() == ".csv":
        raise SystemExit("refusing to write a .csv (Read deny rules target *.csv); use .tsv")
    key = load_key(cfg.pseudo_key_file) if pseudo else b""
    text = _read_text(src, limit_bytes=1 << 31)
    try:
        dialect = csv.Sniffer().sniff(text[:20_000], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader)
    plan: list[tuple[int, str, str]] = []  # (index, name, action)
    for i, name in enumerate(header):
        if name in drop:
            action = "drop"
        elif name in pseudo:
            action = "pseudo"
        elif name in year:
            action = "year"
        elif name in keep:
            action = "keep"
        elif cfg.is_pii_column(name):
            action = "drop"
        else:
            action = "keep"
        plan.append((i, name, action))
    kept = [(i, n, a) for i, n, a in plan if a != "drop"]
    out.parent.mkdir(parents=True, exist_ok=True)
    n_rows = 0
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow([f"{n}_pseudo" if a == "pseudo" else (f"{n}_year" if a == "year" else n) for _, n, a in kept])
        for row in reader:
            outrow = []
            for i, n, a in kept:
                v = row[i] if i < len(row) else ""
                if a == "pseudo":
                    v = pseudo_id(key, v) if v.strip() else ""
                elif a == "year":
                    m = _YEAR_RE.match(v.strip())
                    v = m.group(1) if m else ""
                outrow.append(v)
            w.writerow(outrow)
            n_rows += 1
    summary = {
        "source": src.name,
        "out": str(out),
        "rows": n_rows,
        "columns": [{"name": n, "action": a} for _, n, a in plan],
    }
    out.with_suffix(out.suffix + ".schema.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(prog="safeify-csv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pseudo", action="append", default=[], metavar="COL")
    ap.add_argument("--year", action="append", default=[], metavar="COL")
    ap.add_argument("--keep", action="append", default=[], metavar="COL")
    ap.add_argument("--drop", action="append", default=[], metavar="COL")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()
    cfg = Config.load(a.config)
    s = safeify(cfg, a.src, a.out, a.pseudo, a.year, a.keep, a.drop)
    # Print only the plan, never values.
    print(json.dumps(s, ensure_ascii=False, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
