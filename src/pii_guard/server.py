"""pii-guard daemon: HTTP on 127.0.0.1 only.

POST /mask        {"site": "prompt", "texts": ["..."]}      -> {"texts": [...], "hits": {...}, "tainted": false}
POST /unmask      {"texts": ["..."]}                         -> {"texts": [...]}      (display / support-intake only)
POST /check-args  {"tool": "Bash", "args": {...}}            -> {"deny": bool, "kinds": [...]}
GET  /healthz                                                -> {"ok": true, "dictionary": {...}, "vault": n, ...}
POST /dict/reload                                            -> {"ok": true, "entries": n}

Values are never written to the log or returned in reasons.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import tomllib
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .dictionary import Dictionary
from .masker import Masker
from .vault import Vault

DEFAULT_CONFIG = Path("~/.config/safe-data/config.toml").expanduser()
MAX_BODY = 4 * 1024 * 1024
# the model-facing unmask callers; anything else gets 403
UNMASK_CALLERS = {"ui.render", "support-intake", "slack-safe", "cli"}


class State:
    def __init__(self, cfg: dict) -> None:
        base = Path(cfg.get("dir", "~/.config/safe-data")).expanduser()
        self.dict_path = Path(cfg.get("dictionary", base / "dictionary.json")).expanduser()
        self.vault_path = Path(cfg.get("vault", base / "vault.sqlite")).expanduser()
        self.vault_key = Path(cfg.get("vault_key", base / "vault.key")).expanduser()
        self.patient_id = cfg.get("patient_id_regex")
        self.allow_emails = list(cfg.get("allow_email_domains", []))
        self.host = cfg.get("host", "127.0.0.1")
        self.port = int(cfg.get("port", 8787))
        self.lock = threading.Lock()
        self.started = time.time()
        self.calls = Counter()
        self.reload()

    def reload(self) -> None:
        d = Dictionary.load(self.dict_path)
        v = Vault(self.vault_path, self.vault_key)
        with self.lock:
            self.dictionary = d
            self.vault = v
            self.masker = Masker(d, v, patient_id=self.patient_id, allow_emails=self.allow_emails)


def load_config(path: Path | None) -> dict:
    p = Path(path or os.environ.get("SAFE_DATA_CONFIG") or DEFAULT_CONFIG).expanduser()
    if not p.exists():
        return {}
    return tomllib.loads(p.read_text(encoding="utf-8")).get("pii_guard", {})


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"pii-guard/{__version__}"

        def log_message(self, fmt, *args):  # noqa: D401 - silence default access log (it would echo paths only, but keep quiet)
            pass

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self._json(413, {"error": "body too large"})
                return None
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                self._json(400, {"error": "invalid json"})
                return None

        def do_GET(self) -> None:
            if self.path != "/healthz":
                return self._json(404, {"error": "not found"})
            with state.lock:
                self._json(200, {
                    "ok": True, "version": __version__,
                    "dictionary": {"entries": len(state.dictionary), "persons": state.dictionary.persons,
                                   "generated": state.dictionary.generated, "path": str(state.dict_path)},
                    "vault": state.vault.count(),
                    "uptime_s": int(time.time() - state.started),
                    "calls": dict(state.calls),
                })

        def do_POST(self) -> None:
            state.calls[self.path] += 1
            if self.path == "/dict/reload":
                state.reload()
                return self._json(200, {"ok": True, "entries": len(state.dictionary)})
            body = self._body()
            if body is None:
                return
            if self.path == "/mask":
                site = str(body.get("site", "data"))
                texts = body.get("texts") or []
                if not isinstance(texts, list):
                    return self._json(400, {"error": "texts must be a list"})
                hits: Counter = Counter()
                out = []
                t0 = time.time()
                with state.lock:
                    for t in texts:
                        r = state.masker.mask(str(t), site=site)
                        hits.update(r.hits)
                        out.append(r.text)
                return self._json(200, {"texts": out, "hits": dict(hits), "tainted": False, "ms": int((time.time() - t0) * 1000)})
            if self.path == "/unmask":
                caller = self.headers.get("X-Caller", "")
                if caller not in UNMASK_CALLERS:
                    return self._json(403, {"error": "unmask is for display and local MCPs only"})
                texts = body.get("texts") or []
                with state.lock:
                    return self._json(200, {"texts": [state.vault.unmask(str(t)) for t in texts]})
            if self.path == "/check-args":
                args = body.get("args")
                text = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
                with state.lock:
                    r = state.masker.mask(text, site="prompt")
                kinds = sorted(k for k in r.hits if k.startswith("DICT_") or k in ("PATIENT_ID", "MYNUMBER"))
                return self._json(200, {"deny": bool(kinds), "kinds": kinds})
            return self._json(404, {"error": "not found"})

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(prog="pii-guard")
    ap.add_argument("--config", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--check", action="store_true", help="load config/dictionary and exit")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.port:
        cfg["port"] = args.port
    state = State(cfg)
    if args.check:
        print(json.dumps({"dictionary_entries": len(state.dictionary), "persons": state.dictionary.persons,
                          "vault": str(state.vault_path), "port": state.port}, ensure_ascii=False))
        return
    srv = ThreadingHTTPServer((state.host, state.port), make_handler(state))
    print(f"pii-guard {__version__} listening on http://{state.host}:{state.port} (dict entries={len(state.dictionary)})", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
