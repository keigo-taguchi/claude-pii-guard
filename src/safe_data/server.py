"""safe-data MCP server (stdio).

Tools
-----
schema_describe   columns of the PII-free views + synthetic sample rows
files_describe    files in the PII inbox: id, inferred schema, PII flags (no values)
sql_run           one SELECT against the PII-free schema, read-only, row-capped,
                  small counts suppressed
py_run            run an analysis script in a network-less container against
                  inbox files; only a small JSON result comes back, and it is
                  rejected if it contains any protected value
fixture_make      synthetic rows shaped like a view or a file

Every tool returns a dict with ok=true/false. Errors are returned as data, never
as MCP isError results, so a failure can't carry raw output into the context.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Config
from .files import describe_file, list_inbox
from .guards import (
    cap_output,
    check_sql,
    encoded_blob_hits,
    suppress_small_cells,
    withhold_encoded_blobs,
    wrap_limit,
)
from .pseudo import generate_key_file
from .synth import synth_rows

INSTRUCTIONS = """safe-data は個人情報を含むデータを「手元で」処理し、個人が出てこない結果だけを返すサーバーです。
- 生データは返しません。schema_describe / files_describe で形を確認し、sql_run か py_run で集計してください。
- 人は user_pseudo_id（擬似ID）で指します。実名・メール・電話・患者IDを引数や SQL に書かないでください。
- py_run の結果に保護対象の値が混ざると拒否されます。行を返すのではなく集計してください。
- 少数の集計セル（n<11）は null になります。
"""

cfg = Config.load()
mcp = MCPServer(name="safe-data", version=__version__, instructions=INSTRUCTIONS)


def _ok(**kw: Any) -> dict[str, Any]:
    out = {"ok": True, **kw}
    return cap_output(out, cfg.max_output_chars)


def _err(message: str, **kw: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **kw}


def guarded(fn):
    """Never let an exception escape a tool: an MCP isError result would carry
    a traceback (and whatever it quotes) straight into Claude's context."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return _err(f"internal error in {fn.__name__}: {type(e).__name__}")

    return wrapper


# ------------------------------------------------------------------ database

def _connect():
    import psycopg

    conn = psycopg.connect(f"service={cfg.db_service}", autocommit=False, connect_timeout=10)
    conn.read_only = True
    return conn


def _view_columns(conn, schema: str) -> dict[str, list[tuple[str, str]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.table_name, c.column_name, c.data_type
            FROM information_schema.columns c
            WHERE c.table_schema = %s
            ORDER BY c.table_name, c.ordinal_position
            """,
            (schema,),
        )
        out: dict[str, list[tuple[str, str]]] = {}
        for table, col, dtype in cur.fetchall():
            out.setdefault(table, []).append((col, dtype))
    return out


@mcp.tool()
@guarded
def schema_describe(view: str | None = None) -> dict[str, Any]:
    """PII を除いたビュー（claude スキーマ）の列定義と、Faker で作ったダミー行を返す。実データは返さない。

    Args:
        view: 特定のビュー名。省略時は全ビュー。
    """
    try:
        with _connect() as conn:
            cols = _view_columns(conn, cfg.db_schema)
    except Exception as e:  # noqa: BLE001 - surfaced as data, not as isError
        return _err(f"database unavailable: {type(e).__name__}", hint=f"PGSERVICE={cfg.db_service}; see db/README.md")
    if view:
        if view not in cols:
            return _err(f"no such view in schema {cfg.db_schema}: {view}", available=sorted(cols))
        cols = {view: cols[view]}
    views = []
    for name, columns in cols.items():
        views.append({
            "view": f"{cfg.db_schema}.{name}",
            "columns": [{"name": c, "type": t} for c, t in columns],
            "sample_synthetic": synth_rows(columns, 5),
        })
    return _ok(schema=cfg.db_schema, views=views, note="sample rows are synthetic (Faker ja_JP)")


@mcp.tool()
@guarded
def sql_run(sql: str, max_rows: int = 50) -> dict[str, Any]:
    """claude スキーマの PII 無しビューに対して SELECT を1本実行する（読み取り専用・行数上限・少数セル抑止）。

    Args:
        sql: SELECT または WITH で始まる1文。SELECT * は不可。
        max_rows: 返す最大行数（上限は設定値）。
    """
    reason = check_sql(sql)
    if reason:
        return _err(f"rejected: {reason}")
    n = max(1, min(int(max_rows), cfg.max_rows))
    wrapped = wrap_limit(sql, n + 1)
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = '30s'")
                cur.execute(f"SET LOCAL search_path = {cfg.db_schema}, pg_catalog")
                cur.execute(wrapped)
                columns = [d.name for d in cur.description or []]
                rows = [list(r) for r in cur.fetchall()]
            conn.rollback()
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0][:200]
        return _err(f"query failed: {type(e).__name__}: {msg}")
    more = len(rows) > n
    rows = rows[:n]
    rows, suppressed = suppress_small_cells(columns, rows, cfg.small_cell)
    return _ok(columns=columns, rows=rows, row_count=len(rows), more_rows=more,
               suppressed_cells=suppressed, small_cell_threshold=cfg.small_cell)


# --------------------------------------------------------------------- files

@mcp.tool()
@guarded
def files_describe() -> dict[str, Any]:
    """PII inbox（Claude が直接読めない場所）にあるファイルの一覧と推定スキーマを返す。値は返さない。
    ファイルは id で参照する（py_run の inputs に渡す）。"""
    files = list_inbox(cfg)
    if not files:
        return _ok(inbox=str(cfg.pii_inbox), files=[], note="inbox is empty or missing")
    described = []
    for fid, path in files.items():
        try:
            described.append(describe_file(cfg, path))
        except Exception as e:  # noqa: BLE001
            described.append({"id": fid, "error": f"could not describe: {type(e).__name__}"})
    return _ok(inbox=str(cfg.pii_inbox), files=described)


@mcp.tool()
@guarded
def fixture_make(source: str, n: int = 5) -> dict[str, Any]:
    """ビュー名またはファイル id と同じ形の合成データ（Faker）を n 行返す。テストやスクリプト開発用。

    Args:
        source: claude スキーマのビュー名、または files_describe の id。
        n: 行数（最大 50）。
    """
    n = max(1, min(int(n), 50))
    files = list_inbox(cfg)
    if source in files:
        info = describe_file(cfg, files[source])
        columns = [(c["name"], c["type"]) for c in info.get("columns", [])]
        if not columns:
            return _err("file has no describable columns")
        return _ok(source=source, rows=synth_rows(columns, n), note="synthetic")
    try:
        with _connect() as conn:
            cols = _view_columns(conn, cfg.db_schema)
    except Exception as e:  # noqa: BLE001
        return _err(f"not a file id, and database unavailable: {type(e).__name__}")
    if source not in cols:
        return _err(f"unknown source: {source}", views=sorted(cols), file_ids=sorted(files))
    return _ok(source=source, rows=synth_rows(cols[source], n), note="synthetic")


# --------------------------------------------------------------- read_masked

def _daemon_mask(texts: list[str], site: str = "data") -> list[str]:
    import json as _json
    import urllib.request

    url = os.environ.get("PII_GUARD_URL", "http://127.0.0.1:8787")
    req = urllib.request.Request(url + "/mask", data=_json.dumps({"site": site, "texts": texts}, ensure_ascii=False).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = _json.loads(r.read())
    if not isinstance(data.get("texts"), list) or len(data["texts"]) != len(texts):
        raise RuntimeError("malformed daemon reply")
    return [str(t) for t in data["texts"]]


@mcp.tool()
@guarded
def read_masked(file_id: str, limit: int = 50) -> dict[str, Any]:
    """inbox のテキストファイルの先頭 limit 行を、pii-guard デーモンで擬似化してから返す。
    行単位の中身を見ないと進まないときだけ使う（集計で済むなら sql_run / py_run）。デーモンが止まっていれば返さない。

    Args:
        file_id: files_describe の id。
        limit: 行数（最大 200）。
    """
    files = list_inbox(cfg)
    if file_id not in files:
        return _err(f"unknown file id: {file_id}", file_ids=sorted(files))
    p = files[file_id]
    if p.suffix.lower() not in (".csv", ".tsv", ".txt", ".log", ".jsonl", ".md"):
        return _err("read_masked supports text files only; use py_run for other formats")
    n = max(1, min(int(limit), 200))
    from .files import _read_text

    lines = _read_text(p).splitlines()[:n]
    try:
        masked = _daemon_mask(lines, site="data")
    except Exception as e:  # noqa: BLE001
        return _err(f"pii-guard daemon unavailable ({type(e).__name__}); nothing returned", hint="scripts/pii-guard-launchd.sh install")
    return _ok(file_id=file_id, lines=masked, line_count=len(masked), note="pseudonymized by pii-guard; placeholders like [P12_NAME] are stable per person")


# -------------------------------------------------------------------- py_run

def _docker() -> str | None:
    return shutil.which("docker")


def _resolve_script(script: str) -> Path | None:
    base = cfg.analysis_dir.resolve()
    p = (base / script).resolve() if not Path(script).is_absolute() else Path(script).resolve()
    if base not in p.parents and p != base:
        return None
    if not p.is_file():
        return None
    return p


@mcp.tool()
@guarded
def py_run(script: str, inputs: list[str] | None = None) -> dict[str, Any]:
    """analysis/ 配下のスクリプトを、ネットワーク無しのコンテナで inbox ファイルに対して実行し、小さな JSON 結果だけ返す。

    スクリプト内では `import sd` して `df = sd.load("<file id>")` で読み、`sd.result({...})` で結果を返す。
    print 出力は返らない。結果に保護対象の値（PII 列の値）が含まれると拒否される。

    Args:
        script: analysis ディレクトリからの相対パス（例: retention.py）。
        inputs: files_describe で得た id のリスト。
    """
    inputs = inputs or []
    docker = _docker()
    if not docker:
        return _err("docker is not available", hint="start colima / Docker Desktop, then build: docker build -t safe-data-runner docker/runner")
    sp = _resolve_script(script)
    if not sp:
        return _err(f"script must be an existing file under {cfg.analysis_dir}/ (got {script})")
    files = list_inbox(cfg)
    mounts: list[str] = []
    for fid in inputs:
        if fid not in files:
            return _err(f"unknown file id: {fid}", file_ids=sorted(files))
        p = files[fid]
        mounts += ["--mount", f"type=bind,src={p},dst=/data/{fid}{p.suffix.lower()},readonly"]
    name = f"sd-{uuid.uuid4().hex[:12]}"
    cmd = [
        docker, "run", "--rm", "--name", name,
        "--network", "none", "--read-only", "--tmpfs", "/tmp:size=256m",
        "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-e", f"SD_CONFIG={json.dumps(cfg.runner_env(), ensure_ascii=False)}",
        "--mount", f"type=bind,src={sp},dst=/work/script.py,readonly",
        *mounts,
        cfg.docker_image, "python", "/runner/sd_runner.py",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.py_timeout_s, env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), "DOCKER_HOST": os.environ.get("DOCKER_HOST", "")})
    except subprocess.TimeoutExpired:
        subprocess.run([docker, "kill", name], capture_output=True)
        return _err(f"script exceeded {cfg.py_timeout_s}s and was killed")
    if proc.returncode != 0 and not proc.stdout.strip():
        err = proc.stderr.strip().splitlines()
        tail = err[-1][:160] if err else ""
        if "No such image" in proc.stderr or "Unable to find image" in proc.stderr:
            return _err("runner image missing", hint="docker build -t safe-data-runner docker/runner")
        return _err(f"container failed (exit {proc.returncode})", stderr_tail=withhold_encoded_blobs(tail)[0])
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return _err("runner returned no result; make sure the script calls sd.result(...)")
    if not payload.get("ok"):
        return _err(payload.get("error", "rejected by runner"), reason=payload.get("reason"))
    text = json.dumps(payload.get("result"), ensure_ascii=False)
    if encoded_blob_hits(text):
        return _err("result contains long encoded blobs and was withheld", hint="return plain aggregates")
    return _ok(result=payload.get("result"), logs=payload.get("logs", []))


# ---------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(prog="safe-data-mcp")
    ap.add_argument("--init-key", action="store_true", help="create the pseudonym key file and exit")
    ap.add_argument("--check", action="store_true", help="print effective config and exit")
    args = ap.parse_args()
    if args.init_key:
        p = generate_key_file(cfg.pseudo_key_file)
        print(f"pseudo key: {p}", file=sys.stderr)
        return
    if args.check:
        print(json.dumps({k: str(v) for k, v in cfg.__dict__.items()}, ensure_ascii=False, indent=1))
        return
    mcp.run("stdio")


if __name__ == "__main__":
    main()
