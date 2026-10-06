"""Exercises docker/runner/sd_runner.py in-process (no Docker needed).

Requires pandas in the dev environment; skipped otherwise.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "docker" / "runner" / "sd_runner.py"


def _run(tmp_path, script: str, csv_text: str, file_id="abcd1234"):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / f"{file_id}.csv").write_text(csv_text, encoding="utf-8")
    sp = tmp_path / "script.py"
    sp.write_text(script, encoding="utf-8")
    src = RUNNER.read_text(encoding="utf-8").replace('DATA = Path("/data")', f'DATA = Path({str(data)!r})').replace(
        'runpy.run_path("/work/script.py"', f'runpy.run_path({str(sp)!r}'
    )
    patched = tmp_path / "sd_runner_patched.py"
    patched.write_text(src, encoding="utf-8")
    cfg = {"pii_patterns": ["name", "kana", "mail", "tel", "user_id"], "py_result_bytes": 2000, "py_string_max": 32}
    proc = subprocess.run([sys.executable, str(patched)], capture_output=True, text=True,
                          env={"SD_CONFIG": json.dumps(cfg), "PATH": "/usr/bin:/bin"}, timeout=60)
    return json.loads(proc.stdout.strip().splitlines()[-1])


CSV = (
    "user_id,name,kana,mail,tel,plan,amount\n"
    "1001,田中太郎,たなか たろう,taro@example.com,090-1234-5678,pro,120\n"
    "1002,山田花子,やまだ はなこ,hanako@example.com,080-2345-6789,free,30\n"
    "1003,鈴木一郎,すずき いちろう,ichiro@example.com,070-3456-7890,pro,90\n"
)


def test_aggregate_result_passes(tmp_path):
    out = _run(tmp_path, 'import sd\ndf = sd.load("abcd1234")\nprint("secret 田中太郎")\nsd.result({"by_plan": df.groupby("plan")["amount"].sum().to_dict(), "n": len(df)})\n', CSV)
    assert out["ok"] and out["result"]["by_plan"] == {"free": 30, "pro": 210} and out["result"]["n"] == 3
    assert "田中" not in json.dumps(out, ensure_ascii=False)


def test_rows_with_pii_are_rejected(tmp_path):
    out = _run(tmp_path, 'import sd\ndf = sd.load("abcd1234")\nsd.result(df.head(2))\n', CSV)
    assert not out["ok"] and out["reason"]["kind"] == "egress"
    assert "田中" not in json.dumps(out, ensure_ascii=False)


def test_katakana_and_digit_variants_are_caught(tmp_path):
    # kana reading folded to katakana, phone with the hyphens stripped: both must still be caught
    out = _run(tmp_path, 'import sd\ndf = sd.load("abcd1234")\nsd.result({"who": "タナカタロウ", "tel": "tel:09012345678"})\n', CSV)
    assert not out["ok"] and out["reason"]["kind"] == "egress" and out["reason"]["protected_values_matched"] >= 2


def test_missing_result_and_exceptions_are_reported_without_values(tmp_path):
    out = _run(tmp_path, 'import sd\ndf = sd.load("abcd1234")\n', CSV)
    assert not out["ok"] and "sd.result" in out["error"]
    out = _run(tmp_path, 'import sd\ndf = sd.load("abcd1234")\nraise ValueError("boom " + df["name"][0])\n', CSV)
    assert not out["ok"] and out["reason"]["type"] == "ValueError" and "田中" not in json.dumps(out, ensure_ascii=False)


def test_too_large_result_rejected(tmp_path):
    out = _run(tmp_path, 'import sd\nsd.result({"x": list(range(5000))})\n', CSV)
    assert not out["ok"] and out["reason"]["kind"] == "too_large"
