from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Column names that are treated as personal data wherever they appear
# (CSV headers, DataFrame columns). Matched case-insensitively as substrings.
DEFAULT_PII_PATTERNS: list[str] = [
    "name", "氏名", "名前", "kana", "カナ", "ふりがな",
    "tel", "phone", "電話", "mobile", "携帯", "fax",
    "mail", "メール",
    "birth", "dob", "生年月日", "誕生日",
    "address", "住所", "zip", "postal", "郵便",
    "patient", "患者", "member", "会員", "customer", "顧客",
    "user_id", "userid", "device_id", "deviceid", "line_id", "slack_id",
    "mynumber", "マイナンバー", "個人番号", "保険", "insurance", "passport", "license", "免許",
    "ip", "ip_address", "ua", "user_agent",
]

DEFAULT_CONFIG_PATH = "~/.config/safe-data/config.toml"


@dataclass
class Config:
    pii_inbox: Path = field(default_factory=lambda: Path("~/PII/inbox").expanduser())
    safe_dir: Path = field(default_factory=lambda: Path("./data/safe"))
    analysis_dir: Path = field(default_factory=lambda: Path("./analysis"))
    db_service: str = "claude_ro"
    db_schema: str = "claude"
    max_rows: int = 50
    max_output_chars: int = 40_000
    small_cell: int = 11
    py_result_bytes: int = 8_192
    py_string_max: int = 64
    py_timeout_s: int = 120
    pii_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_PII_PATTERNS))
    docker_image: str = "safe-data-runner:latest"
    pseudo_key_file: Path = field(default_factory=lambda: Path("~/.config/safe-data/pseudo.key").expanduser())
    source: str = "(defaults)"

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "Config":
        p = Path(path or os.environ.get("SAFE_DATA_CONFIG") or DEFAULT_CONFIG_PATH).expanduser()
        data: dict = {}
        if p.exists():
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        paths = data.get("paths", {})
        db = data.get("db", {})
        lim = data.get("limits", {})
        pii = data.get("pii_columns", {})
        docker = data.get("docker", {})
        pseudo = data.get("pseudo", {})
        cfg = cls(
            pii_inbox=Path(paths.get("pii_inbox", "~/PII/inbox")).expanduser(),
            safe_dir=Path(paths.get("safe_dir", "./data/safe")),
            analysis_dir=Path(paths.get("analysis_dir", "./analysis")),
            db_service=db.get("service", "claude_ro"),
            db_schema=db.get("schema", "claude"),
            max_rows=int(lim.get("max_rows", 50)),
            max_output_chars=int(lim.get("max_output_chars", 40_000)),
            small_cell=int(lim.get("small_cell", 11)),
            py_result_bytes=int(lim.get("py_result_bytes", 8_192)),
            py_string_max=int(lim.get("py_string_max", 64)),
            py_timeout_s=int(lim.get("py_timeout_s", 120)),
            pii_patterns=list(pii.get("patterns", DEFAULT_PII_PATTERNS)),
            docker_image=docker.get("image", "safe-data-runner:latest"),
            pseudo_key_file=Path(pseudo.get("key_file", "~/.config/safe-data/pseudo.key")).expanduser(),
            source=str(p) if p.exists() else "(defaults)",
        )
        return cfg

    def is_pii_column(self, name: str) -> bool:
        n = name.strip().lower()
        n_compact = re.sub(r"[\s_\-]", "", n)
        for pat in self.pii_patterns:
            p = pat.lower()
            if p in n or re.sub(r"[\s_\-]", "", p) in n_compact:
                return True
        return False

    def runner_env(self) -> dict:
        """Subset of settings shipped into the py_run container."""
        return {
            "pii_patterns": self.pii_patterns,
            "py_result_bytes": self.py_result_bytes,
            "py_string_max": self.py_string_max,
        }
