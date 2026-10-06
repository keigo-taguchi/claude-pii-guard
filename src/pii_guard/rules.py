"""Deterministic rules for Japanese identifiers. Each rule yields (kind, start, end)
on the *light-normalized* text. Context gates keep false positives low: a 12-digit
number is a My Number only next to the word, a date is a DOB only next to 生年月日.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

Span = tuple[str, int, int]

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 固定・携帯・IP・フリーダイヤル・ナビダイヤル、+81、ハイフン/空白/括弧区切り
PHONE = re.compile(
    r"(?<![\d-])(?:\+81[-\s]?\d{1,4}|0(?:[1-9]\d{0,3}))[-\s(]?\d{1,4}[-\s)]?\d{3,4}(?![\d-])"
)
ZIP = re.compile(r"〒\s?\d{3}-?\d{4}|(?<![\d-])\d{3}-\d{4}(?![\d-])")
ZIP_CONTEXT = re.compile(r"〒|郵便|住所|所在地|お届け先|ご住所")
MYNUMBER = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
MYNUMBER_CONTEXT = re.compile(r"マイナンバー|個人番号|my\s*number", re.I)
DOB_WESTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}\s?[年/.-]\s?\d{1,2}\s?[月/.-]\s?\d{1,2}\s?日?")
DOB_WAREKI = re.compile(r"(?:令和|平成|昭和|大正|明治|[RHSTM])\s?(?:元|\d{1,2})年\s?\d{1,2}月\s?\d{1,2}日")
DOB_CONTEXT = re.compile(r"生年月日|生まれ|誕生日|DOB|birth", re.I)
# 都道府県 → 市区町村郡 → 丁目/番地/号 を 2 階層以上含むもの
ADDRESS = re.compile(
    r"(?:北海道|東京都|京都府|大阪府|[一-龥]{2,3}県)"
    r"[一-龥ぁ-んァ-ヶー]{1,10}(?:市|区|町|村|郡)"
    r"[一-龥ぁ-んァ-ヶー]{0,12}"
    r"(?:[0-9一二三四五六七八九十]{1,4}(?:丁目|番地|番|号|-)){1,3}"
    r"[0-9一二三四五六七八九十]{0,4}"
    r"(?:\s?[一-龥ぁ-んァ-ヶーA-Za-z0-9]{1,20}(?:ビル|マンション|ハイツ|コーポ|荘|号室|階|F))?"
)
CONTEXT_WINDOW = 40


def _near(ctx: re.Pattern[str], text: str, start: int, end: int) -> bool:
    lo, hi = max(0, start - CONTEXT_WINDOW), min(len(text), end + CONTEXT_WINDOW)
    return bool(ctx.search(text[lo:hi]))


def _looks_like_code_id(text: str, start: int, end: int) -> bool:
    """AWS account ids, ARNs, hashes, URIs: 12 digits that are not a My Number."""
    lo, hi = max(0, start - 12), min(len(text), end + 2)
    window = text[lo:hi]
    return bool(re.search(r"arn:|://|[0-9a-f]{20,}|aws|account", window, re.I))


def find_spans(text: str, patient_id: re.Pattern[str] | None = None, allow_emails: Iterable[str] = ()) -> list[Span]:
    spans: list[Span] = []
    allow = {a.lower() for a in allow_emails}
    for m in EMAIL.finditer(text):
        v = m.group(0).lower()
        domain = v.rsplit("@", 1)[-1]
        if domain in allow or domain.startswith("example.") or domain.endswith(".invalid") or domain.endswith(".test"):
            continue
        spans.append(("EMAIL", m.start(), m.end()))
    for m in PHONE.finditer(text):
        core = re.sub(r"\D", "", m.group(0))
        if 10 <= len(core) <= 12:
            spans.append(("PHONE", m.start(), m.end()))
    for m in ZIP.finditer(text):
        if m.group(0).startswith("〒") or _near(ZIP_CONTEXT, text, m.start(), m.end()):
            spans.append(("ZIP", m.start(), m.end()))
    for m in MYNUMBER.finditer(text):
        if _near(MYNUMBER_CONTEXT, text, m.start(), m.end()) and not _looks_like_code_id(text, m.start(), m.end()):
            spans.append(("MYNUMBER", m.start(), m.end()))
    for pat in (DOB_WESTERN, DOB_WAREKI):
        for m in pat.finditer(text):
            if _near(DOB_CONTEXT, text, m.start(), m.end()):
                spans.append(("DOB", m.start(), m.end()))
    for m in ADDRESS.finditer(text):
        spans.append(("ADDRESS", m.start(), m.end()))
    if patient_id is not None:
        for m in patient_id.finditer(text):
            spans.append(("PATIENT_ID", m.start(), m.end()))
    return spans
