"""Text normalization shared by the dictionary and the rules.

Variant kanji (異体字) are mapped to one representative so 髙橋 and 高橋 match the
same dictionary entry; NFKC covers width and compatibility forms; hiragana is
folded to katakana. The same function is applied to dictionary values and to the
text under inspection.
"""
from __future__ import annotations

import re
import unicodedata

# 異体字・旧字体の等価クラス（代表字に写像）。NFKC では正規化されないもの。
_VARIANTS = {
    "髙": "高", "﨑": "崎", "嵜": "崎", "齋": "斎", "齊": "斎", "斉": "斎", "邊": "辺", "邉": "辺",
    "澁": "渋", "國": "国", "廣": "広", "嶋": "島", "櫻": "桜", "眞": "真", "濵": "浜", "濱": "浜",
    "萬": "万", "冨": "富", "德": "徳", "瀨": "瀬", "龍": "竜", "惠": "恵", "條": "条", "彌": "弥",
    "禮": "礼", "吉": "吉", "舘": "館", "藏": "蔵", "壽": "寿", "實": "実", "澤": "沢", "櫛": "櫛",
    "黑": "黒", "繫": "繋", "靜": "静", "鐵": "鉄", "榮": "栄", "與": "与", "會": "会", "學": "学",
    "縣": "県", "關": "関", "靑": "青", "淸": "清", "晴": "晴", "﨑": "崎",
}
_VARIANT_TABLE = str.maketrans(_VARIANTS)
_WS_RE = re.compile(r"[\s　]+")
_DIGITS_RE = re.compile(r"\d")


def fold_kana(s: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)


def canon(s: str) -> str:
    """Canonical comparison form: NFKC, variant kanji folded, kana folded, lowercase, no whitespace."""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_VARIANT_TABLE)
    s = fold_kana(s)
    return _WS_RE.sub("", s).lower()


def light(s: str) -> str:
    """Light form used to rewrite text in place: NFKC + variant folding, nothing else.

    Keeps word boundaries and case so masked text stays readable.
    """
    return unicodedata.normalize("NFKC", s).translate(_VARIANT_TABLE)


def digits(s: str) -> str:
    return "".join(_DIGITS_RE.findall(unicodedata.normalize("NFKC", s)))


def value_forms(value: str) -> set[str]:
    """Forms a dictionary value may appear in inside text (after `light`)."""
    v = value.strip()
    if not v:
        return set()
    base = light(v)
    forms = {base, base.lower(), fold_kana(base), fold_kana(base).lower()}
    forms |= {_WS_RE.sub("", f) for f in list(forms)}
    d = digits(base)
    if len(d) >= 7:
        forms.add(d)
    return {f for f in forms if f}
