import json
import os

import pytest

from safe_data.config import Config
from safe_data.files import describe_file, file_id
from safe_data.pseudo import generate_key_file, load_key, pseudo_id


def test_pseudo_is_deterministic_and_keyed(tmp_path):
    k1 = load_key(generate_key_file(tmp_path / "a.key"))
    k2 = load_key(generate_key_file(tmp_path / "b.key"))
    assert pseudo_id(k1, 12345) == pseudo_id(k1, "12345")
    assert pseudo_id(k1, 12345) != pseudo_id(k2, 12345)
    assert len(pseudo_id(k1, 1)) == 16


def test_key_file_must_be_private(tmp_path):
    p = generate_key_file(tmp_path / "k.key")
    os.chmod(p, 0o644)
    with pytest.raises(PermissionError):
        load_key(p)


def test_pii_column_patterns():
    cfg = Config()
    for col in ["name", "氏名", "user_name", "Tel", "電話番号", "e-mail", "birth_date", "住所", "patient_id", "user_id", "Device-Id"]:
        assert cfg.is_pii_column(col), col
    for col in ["app_version", "plan", "category", "created_day", "n_users", "status"]:
        assert not cfg.is_pii_column(col), col


def test_config_loads_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[paths]\npii_inbox = "/tmp/inbox"\n[limits]\nmax_rows = 7\n', encoding="utf-8")
    cfg = Config.load(p)
    assert str(cfg.pii_inbox) == "/tmp/inbox" and cfg.max_rows == 7 and cfg.small_cell == 11


def test_describe_file_flags_pii_without_leaking_values(tmp_path):
    csv = tmp_path / "customers.csv"
    csv.write_text(
        "id,name,mail,plan,signup\n"
        "1,田中太郎,taro@example.com,pro,2026-01-02\n"
        "2,山田花子,hanako@example.com,free,2026-02-03\n",
        encoding="utf-8",
    )
    cfg = Config(pii_inbox=tmp_path)
    info = describe_file(cfg, csv)
    cols = {c["name"]: c for c in info["columns"]}
    assert cols["name"]["pii"] and cols["mail"]["pii"] and not cols["plan"]["pii"]
    assert cols["signup"]["type"] == "date" and cols["id"]["type"] == "int"
    dumped = json.dumps(info, ensure_ascii=False)
    for secret in ("田中", "山田", "taro@", "hanako@", "customers"):
        assert secret not in dumped
    assert info["id"] == file_id(csv)
