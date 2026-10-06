import importlib.util
import json
import sys
from pathlib import Path

from safe_data.config import Config
from safe_data.etl import safeify
from safe_data.pseudo import generate_key_file, load_key, pseudo_id

ROOT = Path(__file__).resolve().parents[1]


def _load_merge():
    spec = importlib.util.spec_from_file_location("merge_settings", ROOT / "settings" / "merge_settings.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["merge_settings"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_template_is_valid_json_and_uses_documented_keys():
    t = json.loads((ROOT / "settings" / "phase0.settings.json").read_text(encoding="utf-8"))
    assert t["permissions"]["defaultMode"] == "auto"
    assert t["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert t["enableArtifact"] is False
    assert t["sandbox"]["network"]["allowLocalBinding"] is True
    assert all(isinstance(r, str) for r in t["permissions"]["deny"])
    # no broad rules that break the dev toolchain (critique findings)
    deny = t["permissions"]["deny"]
    assert "Read(~/Library/**)" not in deny and "Read(~/.claude/projects/**)" not in deny
    assert "*.amazonaws.com" not in t["sandbox"]["network"]["deniedDomains"]
    assert "*.googleapis.com" not in t["sandbox"]["network"]["deniedDomains"]


def test_merge_drops_unresolved_tokens_and_unions_lists(tmp_path):
    m = _load_merge()
    template = {"permissions": {"deny": ["Read(~/PII/**)", "${gmail}", "${slack}"]}, "x": {"y": 1}}
    dropped = []
    sub = m.substitute(template, {"gmail": "mcp__abc"}, dropped)
    assert sub["permissions"]["deny"] == ["Read(~/PII/**)", "mcp__abc"]
    assert dropped == ["${slack}"]
    base = {"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Read(./.env)"]}, "x": {"y": 0, "z": 2}, "model": "opus"}
    overrides = []
    merged = m.merge(base, sub, overrides=overrides)
    assert merged["permissions"]["deny"] == ["Read(./.env)", "Read(~/PII/**)", "mcp__abc"]
    assert merged["permissions"]["allow"] == ["Bash(ls:*)"]
    assert merged["x"] == {"y": 1, "z": 2} and merged["model"] == "opus"
    assert overrides == ["x.y: 0 -> 1"]


def test_safeify_drops_pii_and_pseudonymizes(tmp_path):
    key_path = generate_key_file(tmp_path / "k.key")
    cfg = Config(pii_inbox=tmp_path, pseudo_key_file=key_path)
    src = tmp_path / "users.csv"
    src.write_text(
        "user_id,name,mail,birth_date,plan\n"
        "1001,田中太郎,taro@example.com,1990-05-06,pro\n"
        "1002,山田花子,hanako@example.com,1985-12-31,free\n",
        encoding="utf-8",
    )
    out = tmp_path / "safe" / "users.tsv"
    summary = safeify(cfg, src, out, pseudo=["user_id"], year=["birth_date"], keep=[], drop=[])
    text = out.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "user_id_pseudo\tbirth_date_year\tplan"
    assert pseudo_id(load_key(key_path), "1001") in text and "1990\tpro" in text
    for secret in ("田中", "taro@", "1001", "1990-05"):
        assert secret not in text
    actions = {c["name"]: c["action"] for c in summary["columns"]}
    assert actions == {"user_id": "pseudo", "name": "drop", "mail": "drop", "birth_date": "year", "plan": "keep"}
    assert (tmp_path / "safe" / "users.tsv.schema.json").exists()
