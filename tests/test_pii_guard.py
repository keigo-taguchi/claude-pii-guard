import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pii_guard.dictionary import Dictionary
from pii_guard.masker import Masker
from pii_guard.normalize import canon, value_forms
from pii_guard.rules import find_spans
from pii_guard.server import State, make_handler
from pii_guard.vault import Vault

DICT = {
    "generated": "t",
    "persons": [
        {"id": "P1", "names": ["田中太郎", "たなか たろう", "Taro Tanaka"], "emails": ["taro@acme.co.jp"], "phones": ["090-1234-5678"], "ids": ["PT-000123"]},
        {"id": "P2", "names": ["髙橋花子"], "emails": [], "phones": [], "ids": []},
        {"id": "P3", "names": ["森"], "emails": [], "phones": [], "ids": []},  # too short: must not be indexed
    ],
}


@pytest.fixture
def masker(tmp_path):
    return Masker(Dictionary.from_data(DICT), Vault(tmp_path / "v.sqlite"), patient_id=r"PT-\d{6}")


def test_normalize_variants_and_kana():
    assert canon("髙橋　花子") == canon("高橋花子")
    assert "タナカタロウ" in value_forms("たなか たろう")
    assert "09012345678" in value_forms("０９０－１２３４－５６７８")


def test_dictionary_skips_short_and_orders_longest_first():
    d = Dictionary.from_data(DICT)
    forms = [e.form for e in d.entries]
    assert "森" not in forms
    assert forms == sorted(forms, key=len, reverse=True)


def test_masker_known_person_is_person_scoped_and_consistent(masker):
    r = masker.mask("担当: 田中太郎（タナカ タロウ）、Taro Tanaka、tel 090-1234-5678、PT-000123", site="prompt")
    assert "田中" not in r.text and "Tanaka" not in r.text and "1234" not in r.text and "000123" not in r.text
    assert "[P1_NAME]" in r.text and "[P1_PHONE]" in r.text and "[P1_ID]" in r.text
    again = masker.mask("田中太郎", site="prompt")
    assert again.text == "[P1_NAME]"


def test_masker_variant_kanji_matches_dictionary(masker):
    r = masker.mask("高橋花子さん", site="data")
    assert r.text.startswith("[P2_NAME]")


def test_rules_unknown_values_get_type_tokens(masker):
    text = "山田様 生年月日 1990年5月6日 住所 東京都千代田区丸の内1-2-3 ビルA 連絡先 080-9876-5432 yamada@gmail.com マイナンバー 1234 5678 9012"
    r = masker.mask(text, site="data")
    for secret in ("1990", "丸の内", "9876", "yamada@", "5678 9012"):
        assert secret not in r.text, secret
    assert set(r.hits) >= {"DOB", "ADDRESS", "PHONE", "EMAIL", "MYNUMBER"}


def test_rules_need_context_for_mynumber_dob_zip():
    spans = find_spans("order 1234 5678 9012 shipped 2024-05-06 code 123-4567 aws account 123456789012")
    kinds = {k for k, _, _ in spans}
    assert "MYNUMBER" not in kinds and "DOB" not in kinds and "ZIP" not in kinds


def test_code_site_masks_only_dictionary_and_ids(masker):
    code = "const owner = '田中太郎'; const tel = '080-1111-2222'; // PT-000123 東京都千代田区丸の内1-2-3"
    r = masker.mask(code, site="code")
    assert "田中太郎" not in r.text and "PT-000123" not in r.text
    assert "080-1111-2222" in r.text and "丸の内" in r.text  # untouched so Edit still matches


def test_safe_site_is_untouched(masker):
    r = masker.mask("田中太郎", site="safe")
    assert r.text == "田中太郎" and not r.hits


def test_vault_unmask_round_trip(masker):
    r = masker.mask("田中太郎 and taro@acme.co.jp", site="prompt")
    assert masker.vault.unmask(r.text) == "田中太郎 and taro@acme.co.jp"


@pytest.fixture
def server(tmp_path):
    dict_path = tmp_path / "dictionary.json"
    dict_path.write_text(json.dumps(DICT, ensure_ascii=False), encoding="utf-8")
    state = State({"dictionary": str(dict_path), "vault": str(tmp_path / "v.sqlite"), "port": 0, "patient_id_regex": r"PT-\d{6}"})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _post(url, path, body, headers=None):
    req = urllib.request.Request(url + path, data=json.dumps(body, ensure_ascii=False).encode(), headers={"content-type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_mask_check_unmask(server):
    code, body = _post(server, "/mask", {"site": "prompt", "texts": ["田中太郎 090-1234-5678", "plain"]})
    assert code == 200 and body["texts"] == ["[P1_NAME] [P1_PHONE]", "plain"] and body["hits"]["DICT_NAME"] == 1
    code, body = _post(server, "/check-args", {"tool": "Bash", "args": {"command": "psql -c \"where name='田中太郎'\""}})
    assert code == 200 and body["deny"] is True and body["kinds"] == ["DICT_NAME"]
    code, body = _post(server, "/check-args", {"tool": "Bash", "args": {"command": "ls -la"}})
    assert body["deny"] is False
    code, body = _post(server, "/unmask", {"texts": ["[P1_NAME]"]})
    assert code == 403
    code, body = _post(server, "/unmask", {"texts": ["[P1_NAME]"]}, {"X-Caller": "ui.render"})
    assert code == 200 and body["texts"] == ["田中太郎"]
    with urllib.request.urlopen(server + "/healthz", timeout=5) as r:
        h = json.loads(r.read())
    assert h["ok"] and h["dictionary"]["persons"] == 3 and h["vault"] >= 2
