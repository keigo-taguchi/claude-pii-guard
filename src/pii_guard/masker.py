"""Mask text: dictionary first (known people), then rules (unknown identifiers).

Sites decide how aggressive to be:
  prompt / data  : light-normalize the text, dictionary + all rules
  context        : same as data (CLAUDE.md, memory, git status)
  code           : no normalization, dictionary + PATIENT_ID + EMAIL only, so an
                   Edit's old_string still matches the file on disk
  safe           : nothing (already pseudonymized by safe-data / support-intake)
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .dictionary import Dictionary
from .normalize import light
from .rules import find_spans
from .vault import Vault

SITE_FULL = {"prompt", "data", "context", "attachment"}
SITE_CODE = {"code"}
SITE_SKIP = {"safe", "engine"}


@dataclass
class MaskResult:
    text: str
    hits: Counter
    tainted: bool = False


class Masker:
    def __init__(self, dictionary: Dictionary, vault: Vault, patient_id: str | None = None, allow_emails: list[str] | None = None) -> None:
        self.dictionary = dictionary
        self.vault = vault
        self.patient_id = re.compile(patient_id) if patient_id else None
        self.allow_emails = allow_emails or []

    def mask(self, text: str, site: str = "data") -> MaskResult:
        if not text or site in SITE_SKIP:
            return MaskResult(text=text, hits=Counter())
        hits: Counter = Counter()
        work = text if site in SITE_CODE else light(text)
        work = self._apply_dictionary(work, hits, site)
        work = self._apply_rules(work, hits, site)
        return MaskResult(text=work, hits=hits)

    # -- dictionary ---------------------------------------------------------
    def _apply_dictionary(self, text: str, hits: Counter, site: str) -> str:
        if not self.dictionary.entries:
            return text
        lowered = text.lower()
        for e in self.dictionary.entries:  # longest first
            if e.form not in lowered and e.form not in text:
                continue
            token = self.vault.token_for(e.kind, e.form, person=e.person)
            # case-insensitive replace preserving everything else
            pattern = re.compile(re.escape(e.form), re.IGNORECASE)
            text, n = pattern.subn(token, text)
            if n:
                hits[f"DICT_{e.kind}"] += n
                lowered = text.lower()
        return text

    # -- rules --------------------------------------------------------------
    def _apply_rules(self, text: str, hits: Counter, site: str) -> str:
        spans = find_spans(text, patient_id=self.patient_id, allow_emails=self.allow_emails)
        if site in SITE_CODE:
            spans = [s for s in spans if s[0] in ("PATIENT_ID", "EMAIL")]
        if not spans:
            return text
        # resolve overlaps: earliest start, then longest
        spans.sort(key=lambda s: (s[1], -(s[2] - s[1])))
        merged: list[tuple[str, int, int]] = []
        last_end = -1
        for kind, a, b in spans:
            if a < last_end:
                continue
            # never re-mask an existing token
            if "[" in text[a:b] and "]" in text[a:b]:
                continue
            merged.append((kind, a, b))
            last_end = b
        out: list[str] = []
        pos = 0
        for kind, a, b in merged:
            out.append(text[pos:a])
            out.append(self.vault.token_for(kind, text[a:b]))
            hits[kind] += 1
            pos = b
        out.append(text[pos:])
        return "".join(out)
