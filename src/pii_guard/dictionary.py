"""Known-entity dictionary: the people the company already knows.

File format (~/.config/safe-data/dictionary.json, 0600):
{
  "generated": "2026-10-06T02:00:00",
  "persons": [
    {"id": "P12", "names": ["田中太郎", "たなか たろう", "Taro Tanaka"],
     "emails": ["taro@example.com"], "phones": ["090-1234-5678"], "ids": ["PT-000123"]}
  ]
}

Only full names (3+ chars) are indexed; a single surname such as 原 or 森 would
mask ordinary words. Numeric identifiers need 7+ digits.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import value_forms

KIND_BY_FIELD = {"names": "NAME", "emails": "EMAIL", "phones": "PHONE", "ids": "ID"}
_ROMAJI_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]+$")


@dataclass
class Entry:
    form: str
    person: str
    kind: str


@dataclass
class Dictionary:
    entries: list[Entry] = field(default_factory=list)
    generated: str = ""
    persons: int = 0

    @classmethod
    def load(cls, path: Path) -> "Dictionary":
        path = Path(path).expanduser()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_data(data)

    @classmethod
    def from_data(cls, data: dict) -> "Dictionary":
        entries: dict[str, Entry] = {}
        for p in data.get("persons", []):
            pid = str(p.get("id", "")).strip()
            if not pid:
                continue
            for field_name, kind in KIND_BY_FIELD.items():
                for value in p.get(field_name, []) or []:
                    for form in value_forms(str(value)):
                        if kind == "NAME":
                            if _ROMAJI_RE.match(form):
                                if len(form.replace(" ", "")) < 5:
                                    continue
                            elif len(form) < 3:
                                continue
                        elif kind in ("PHONE", "ID") and form.isdigit() and len(form) < 7:
                            continue
                        elif len(form) < 3:
                            continue
                        # longest form wins when two persons share one (rare); keep first
                        entries.setdefault(form, Entry(form=form, person=pid, kind=kind))
        ordered = sorted(entries.values(), key=lambda e: len(e.form), reverse=True)
        return cls(entries=ordered, generated=str(data.get("generated", "")), persons=len(data.get("persons", [])))

    def __len__(self) -> int:
        return len(self.entries)
