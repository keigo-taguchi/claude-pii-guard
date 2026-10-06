"""pii-guard-dict: build dictionary.json from a CSV export or a PostgreSQL query.

    pii-guard-dict --from-csv ~/PII/exports/customers.csv --id user_id --name name --kana kana --email mail --phone tel
    pii-guard-dict --from-pg "service=dict_builder" --sql "select id, name, kana, email, tel from users" \
        --id id --name name --kana kana --email email --phone tel

Writes ~/.config/safe-data/dictionary.json (0600). Person ids become P<n> in
insertion order, so the same input order yields the same placeholders.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_OUT = Path("~/.config/safe-data/dictionary.json").expanduser()


def rows_from_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def rows_from_pg(conninfo: str, sql: str) -> list[dict]:
    import psycopg

    with psycopg.connect(conninfo) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description or []]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def build(rows: list[dict], id_col: str, name_cols: list[str], email_cols: list[str], phone_cols: list[str], extra_id_cols: list[str]) -> dict:
    persons = []
    for i, r in enumerate(rows, 1):
        pid = f"P{i}"
        def vals(cols):
            return [str(r[c]).strip() for c in cols if c in r and r[c] is not None and str(r[c]).strip()]
        ids = vals([id_col] + extra_id_cols)
        p = {"id": pid, "names": vals(name_cols), "emails": vals(email_cols), "phones": vals(phone_cols), "ids": ids}
        if any(p[k] for k in ("names", "emails", "phones", "ids")):
            persons.append(p)
    return {"generated": datetime.now().isoformat(timespec="seconds"), "persons": persons}


def write(data: dict, out: Path) -> None:
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, out)
    os.chmod(out, 0o600)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pii-guard-dict", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-csv", type=Path)
    src.add_argument("--from-pg", metavar="CONNINFO")
    ap.add_argument("--sql", default=None)
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", action="append", default=[])
    ap.add_argument("--kana", action="append", default=[])
    ap.add_argument("--email", action="append", default=[])
    ap.add_argument("--phone", action="append", default=[])
    ap.add_argument("--extra-id", action="append", default=[])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args()
    rows = rows_from_csv(a.from_csv) if a.from_csv else rows_from_pg(a.from_pg, a.sql or "")
    data = build(rows, a.id, a.name + a.kana, a.email, a.phone, a.extra_id)
    write(data, a.out)
    print(json.dumps({"persons": len(data["persons"]), "out": str(a.out)}), flush=True)


if __name__ == "__main__":
    main()
