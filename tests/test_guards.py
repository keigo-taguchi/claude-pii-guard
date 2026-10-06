import json

import pytest

from safe_data.guards import (
    EgressGuard,
    cap_output,
    check_sql,
    encoded_blob_hits,
    normalize_forms,
    suppress_small_cells,
    withhold_encoded_blobs,
    wrap_limit,
)


@pytest.mark.parametrize("sql", [
    "SELECT category, count(*) AS n FROM tickets_safe GROUP BY 1",
    "with t as (select user_pseudo_id, plan from users_safe) select plan, count(*) as n_users from t group by 1",
    "select user_pseudo_id, app_version from users_safe where user_pseudo_id = 'ab12' -- note",
    "SELECT count(*) FROM users_safe",
])
def test_sql_allowed(sql):
    assert check_sql(sql) is None


@pytest.mark.parametrize("sql,needle", [
    ("select * from users_safe", "SELECT *"),
    ("select u.* from users_safe u", "SELECT *"),
    ("select category, * from tickets_safe", "SELECT *"),
    ("delete from users_safe", "SELECT or WITH"),
    ("select 1; drop table users", "one statement"),
    ("select current_setting('x')", "CURRENT_SETTING"),
    ("copy (select 1) to '/tmp/x'", "SELECT or WITH"),
    ("select pg_read_file('/etc/passwd')", "PG_READ_FILE"),
    ("select 1 from information_schema.tables", "INFORMATION_SCHEMA"),
    ("set search_path = public", "SELECT or WITH"),
    ("select pg_sleep(10)", "PG_SLEEP"),
    ("select $$x$$", "dollar"),
    ("", "empty"),
])
def test_sql_rejected(sql, needle):
    reason = check_sql(sql)
    assert reason is not None and needle in reason


def test_wrap_limit():
    w = wrap_limit("select a from t; ", 5)
    assert w.strip().endswith("LIMIT 5") and "select a from t" in w


def test_small_cells():
    cols = ["category", "n_users", "avg_age"]
    rows = [["a", 3, 40.0], ["b", 11, 41.0], ["c", 0, 0.0], ["d", 400, 1.0]]
    out, n = suppress_small_cells(cols, rows, 11)
    assert n == 1
    assert out[0][1] is None and out[1][1] == 11 and out[2][1] == 0 and out[3][1] == 400
    assert out[0][2] == 40.0  # non-count column untouched


def test_normalize_forms_cover_transformations():
    forms = normalize_forms("たなか　たろう")
    assert "タナカタロウ" in forms
    forms = normalize_forms("０９０－１２３４－５６７８")
    assert "09012345678" in forms


def test_egress_guard_catches_variants():
    g = EgressGuard(["田中太郎", "たなか たろう", "090-1234-5678", "taro@example.com", "ab", "12"])
    assert g.check("結果: 田 中 太 郎") == [{"type": "protected_value", "count": 1}]
    assert g.check("タナカタロウ さん") != []
    assert g.check("tel 09012345678") != []
    assert g.check("TARO@EXAMPLE.COM") != []
    assert g.check("ab 12 totally fine") == []  # too short to register
    assert g.check("週次集計 n=42") == []


def test_egress_guard_never_echoes_values():
    g = EgressGuard(["山田花子"])
    hits = g.check("山田花子")
    assert "山田" not in json.dumps(hits, ensure_ascii=False)


def test_encoded_blob_gate():
    blob = "A" * 60
    assert encoded_blob_hits(f"x {blob} y") == 1
    text, n = withhold_encoded_blobs(f"x {blob} y")
    assert n == 1 and "[ENCODED_BLOB_WITHHELD]" in text and blob not in text
    assert encoded_blob_hits("short b64 QUJD") == 0


def test_cap_output_truncates_rows_first():
    obj = {"ok": True, "rows": [[i, "x" * 50] for i in range(2000)]}
    out = cap_output(obj, 5000)
    assert out["truncated"] is True and len(out["rows"]) < 2000 and out["rows_dropped"] > 0
    assert len(json.dumps(out)) <= 5000


def test_cap_output_withholds_when_nothing_to_trim():
    obj = {"ok": True, "blob": "x" * 10_000}
    out = cap_output(obj, 1000)
    assert out["truncated"] and "blob" not in out
