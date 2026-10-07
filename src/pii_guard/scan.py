"""pii-guard-scan: nightly check that nothing protected reached the stored conversation.

    pii-guard-scan                      # ~/.claude/projects/**/*.jsonl, ~/claude-memory, tool-results
    pii-guard-scan --since-hours 24 --paths ~/.claude/projects ~/claude-memory

Every text block is sent to the local daemon's /mask with site=prompt (full
rules + dictionary). A file whose text changes under masking contains protected
values. The report lists files, hit kinds and counts, never the values.
Exit status 1 when anything was found, so a launchd job can alert on it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

DEFAULT_PATHS = ["~/.claude/projects", "~/claude-memory"]
CHUNK = 60_000


def _texts_from_jsonl(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message") if isinstance(d, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    out.append(b["text"])
                elif b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, str):
                        out.append(c)
                    elif isinstance(c, list):
                        out.extend(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        # the structured record the engine keeps beside a tool result
        tur = d.get("toolUseResult") if isinstance(d, dict) else None
        if tur is not None:
            out.append(json.dumps(tur, ensure_ascii=False)[:CHUNK])
    return out


def _mask(url: str, texts: list[str]) -> dict:
    req = urllib.request.Request(url + "/mask", data=json.dumps({"site": "prompt", "texts": texts}, ensure_ascii=False).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def scan(paths: list[Path], url: str, since: float | None) -> tuple[dict[str, Counter], int]:
    findings: dict[str, Counter] = {}
    files = 0
    for root in paths:
        root = root.expanduser()
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix not in (".jsonl", ".md", ".txt", ".json"):
                continue
            if since is not None and p.stat().st_mtime < since:
                continue
            files += 1
            texts = _texts_from_jsonl(p) if p.suffix == ".jsonl" else [p.read_text(encoding="utf-8", errors="replace")]
            hits: Counter = Counter()
            for i in range(0, len(texts), 50):
                batch = [t[:CHUNK] for t in texts[i:i + 50] if t]
                if not batch:
                    continue
                res = _mask(url, batch)
                hits.update({k: v for k, v in res.get("hits", {}).items()})
            if hits:
                findings[str(p)] = hits
    return findings, files


def main() -> int:
    ap = argparse.ArgumentParser(prog="pii-guard-scan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("PII_GUARD_URL", "http://127.0.0.1:8787"))
    ap.add_argument("--paths", nargs="*", default=DEFAULT_PATHS)
    ap.add_argument("--since-hours", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    since = time.time() - a.since_hours * 3600 if a.since_hours else None
    try:
        findings, files = scan([Path(p) for p in a.paths], a.url, since)
    except Exception as e:  # noqa: BLE001
        print(f"pii-guard-scan: daemon unreachable or failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"files_scanned": files, "findings": {k: dict(v) for k, v in findings.items()}}, ensure_ascii=False, indent=1))
    else:
        print(f"scanned {files} files; {len(findings)} with protected values")
        for f, hits in findings.items():
            print(f"  {f}: " + ", ".join(f"{k}×{v}" for k, v in sorted(hits.items())))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
