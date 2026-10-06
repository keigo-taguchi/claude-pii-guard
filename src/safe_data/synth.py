"""Synthetic sample rows (Faker ja_JP). Shapes match the schema; values are fake."""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

from faker import Faker

_fake = Faker("ja_JP")
Faker.seed(20261006)
random.seed(20261006)


def synth_value(column: str, dtype: str) -> Any:
    c = column.lower()
    t = dtype.lower()
    if "pseudo" in c or c.endswith("_hash"):
        return _fake.hexify("^" * 16, upper=False)
    if any(k in c for k in ("name", "氏名", "名前")):
        return _fake.name()
    if "kana" in c or "カナ" in c:
        return _fake.kana_name()
    if "mail" in c:
        return _fake.safe_email()
    if any(k in c for k in ("tel", "phone", "電話")):
        return _fake.phone_number()
    if any(k in c for k in ("address", "住所")):
        return _fake.address()
    if any(k in c for k in ("zip", "postal", "郵便")):
        return _fake.zipcode()
    if any(k in c for k in ("prefecture", "都道府県", "region")):
        return _fake.prefecture()
    if "birth_year" in c or c.endswith("_year"):
        return random.randint(1950, 2010)
    if any(k in c for k in ("birth", "dob", "生年月日")):
        return _fake.date_of_birth(minimum_age=18, maximum_age=90).isoformat()
    if "version" in c:
        return f"{random.randint(1, 4)}.{random.randint(0, 12)}.{random.randint(0, 9)}"
    if "status" in c:
        return random.choice(["active", "paused", "closed"])
    if "plan" in c:
        return random.choice(["free", "standard", "pro"])
    if "category" in c or "カテゴリ" in c:
        return random.choice(["ログインできない", "通知が来ない", "データ同期", "課金", "解約", "その他"])
    if "product" in c:
        return random.choice(["app-a", "app-b", "app-c"])
    if t in ("bool", "boolean"):
        return random.choice([True, False])
    if "timestamp" in t or "datetime" in t:
        return _fake.date_time_this_year().isoformat(timespec="seconds")
    if t == "date" or c.endswith("_day") or c.endswith("_date"):
        return (date.today() - timedelta(days=random.randint(0, 365))).isoformat()
    if any(k in t for k in ("int", "serial", "numeric", "decimal", "real", "double", "float")):
        if is_countish(c):
            return random.randint(11, 400)
        return random.randint(0, 1000)
    if "json" in t:
        return {"k": "v"}
    if "id" in c:
        return random.randint(1, 99999)
    return _fake.word()


def is_countish(c: str) -> bool:
    return c in ("n", "cnt", "count") or c.startswith(("n_", "count_", "num_", "total"))


def synth_rows(columns: list[tuple[str, str]], n: int) -> list[dict[str, Any]]:
    return [{name: synth_value(name, dtype) for name, dtype in columns} for _ in range(n)]
