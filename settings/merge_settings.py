#!/usr/bin/env python3
"""Merge settings/phase0.settings.json into ~/.claude/settings.json.

    python3 settings/merge_settings.py                 # dry run: show what would change
    python3 settings/merge_settings.py --apply         # write, keeping a timestamped backup

Merge rules
  dict   -> recursive
  list   -> union (existing order kept, new items appended)
  scalar -> template wins, and each override is printed
Rules that still contain an unresolved ${token} (connector names you haven't
filled in settings/connectors.local.json) are dropped with a warning: an
unresolved rule would match nothing and silently give false comfort.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN_RE = re.compile(r"\$\{(\w+)\}")


def substitute(obj, mapping: dict[str, str], dropped: list[str]):
    if isinstance(obj, dict):
        return {k: substitute(v, mapping, dropped) for k, v in obj.items()}
    if isinstance(obj, list):
        out = []
        for item in obj:
            if isinstance(item, str) and TOKEN_RE.search(item):
                resolved = TOKEN_RE.sub(lambda m: mapping.get(m.group(1), ""), item)
                if TOKEN_RE.search(resolved) or not resolved.strip() or resolved != item and not resolved:
                    dropped.append(item)
                    continue
                if resolved == "" or resolved.startswith("mcp__") is False and item.strip() in ("${%s}" % t for t in mapping):
                    dropped.append(item)
                    continue
                out.append(resolved)
            else:
                out.append(substitute(item, mapping, dropped))
        return out
    return obj


def merge(base, template, path="", overrides: list[str] | None = None):
    overrides = overrides if overrides is not None else []
    if isinstance(base, dict) and isinstance(template, dict):
        out = dict(base)
        for k, v in template.items():
            out[k] = merge(base.get(k), v, f"{path}.{k}" if path else k, overrides)
        return out
    if isinstance(base, list) and isinstance(template, list):
        out = list(base)
        for item in template:
            if item not in out:
                out.append(item)
        return out
    if base is not None and base != template and not isinstance(template, (dict, list)):
        overrides.append(f"{path}: {json.dumps(base, ensure_ascii=False)} -> {json.dumps(template, ensure_ascii=False)}")
    return copy.deepcopy(template) if template is not None else base


def load_connectors(path: Path | None) -> dict[str, str]:
    p = path or (HERE / "connectors.local.json")
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str) and v.strip()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", type=Path, default=HERE / "phase0.settings.json")
    ap.add_argument("--target", type=Path, default=Path("~/.claude/settings.json").expanduser())
    ap.add_argument("--connectors", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    template = json.loads(a.template.read_text(encoding="utf-8"))
    mapping = load_connectors(a.connectors)
    dropped: list[str] = []
    template = substitute(template, mapping, dropped)
    base = json.loads(a.target.read_text(encoding="utf-8")) if a.target.exists() else {}
    overrides: list[str] = []
    merged = merge(base, template, overrides=overrides)

    # Conflicts: an existing allow rule that a new deny rule shadows (deny wins; just inform).
    allow = set(base.get("permissions", {}).get("allow", []))
    deny = set(merged.get("permissions", {}).get("deny", []))
    shadowed = sorted(r for r in allow if r.replace(":*", " *") in deny or r in deny)

    print(f"template : {a.template}")
    print(f"target   : {a.target} ({'exists' if a.target.exists() else 'new'})")
    print(f"connectors resolved: {sorted(mapping) or 'none'}")
    if dropped:
        print("dropped rules with unresolved connector names (fill settings/connectors.local.json):")
        for d in dropped:
            print(f"  - {d}")
    if overrides:
        print("scalar overrides:")
        for o in overrides:
            print(f"  - {o}")
    if shadowed:
        print("existing allow rules now shadowed by deny (deny wins):")
        for s in shadowed:
            print(f"  - {s}")
    new_deny = [r for r in merged.get("permissions", {}).get("deny", []) if r not in base.get("permissions", {}).get("deny", [])]
    print(f"deny rules added: {len(new_deny)}")

    if not a.apply:
        print("\n(dry run) re-run with --apply to write")
        return 0
    if a.target.exists():
        backup = a.target.with_name(f"settings.json.bak-{datetime.now():%Y%m%d-%H%M%S}")
        backup.write_text(a.target.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"backup   : {backup}")
    a.target.parent.mkdir(parents=True, exist_ok=True)
    a.target.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written  : {a.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
