"""Minimal operator console: a stdlib HTTP server on a background thread that touches only files.

It never imports Playwright (a guard test asserts it) and never talks to the automation thread:
it reads ``intervention.json``, ``state.json`` and the latest screenshot from the run directory,
and every POST appends one line to ``commands.jsonl``. A POST naming an intervention id that is
not the pending one gets 409 and is not written. The automation thread polls the file.

Production design (documented, not built): the same page fronts a CDP/noVNC view of the live
session; the state machine and the command channel do not change.
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from teller.hitl.commands import (
    RESUME_MODES,
    append_command,
    read_commands,
    read_intervention,
    read_state,
)

log = logging.getLogger("teller.hitl.operator")
_HTML = (Path(__file__).parent / "operator.html").read_text(encoding="utf-8")


class OperatorServer:
    def __init__(self, run_dir: Path, *, host: str = "127.0.0.1", port: int = 8787):
        self.run_dir = Path(run_dir)
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> str:
        run_dir = self.run_dir

        class Handler(BaseHTTPRequestHandler):
            server_version = "teller-operator/0.1"

            def log_message(self, fmt: str, *args: object) -> None:  # quiet
                log.debug(fmt, *args)

            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                if path in ("/", "/interventions", "/interventions/"):
                    self._send(200, _HTML.encode(), "text/html; charset=utf-8")
                    return
                if path.endswith(".json") and path.startswith("/interventions/"):
                    req = read_intervention(run_dir) or {}
                    state = read_state(run_dir) or {}
                    cmds, _ = read_commands(run_dir)
                    body = {"intervention": req, "state": {k: state.get(k) for k in ("state", "controller", "handoffs")}, "commands": cmds[-10:]}
                    self._send(200, json.dumps(body).encode())
                    return
                if path.startswith("/interventions/") and not path.endswith(".json"):
                    self._send(200, _HTML.encode(), "text/html; charset=utf-8")
                    return
                if path.startswith("/screenshot"):
                    live = run_dir / "screenshots" / "handoff_live.jpg"
                    req = read_intervention(run_dir) or {}
                    cand = live if live.exists() else (run_dir / req["screenshot"] if req.get("screenshot") else None)
                    if cand and cand.exists():
                        self._send(200, cand.read_bytes(), "image/jpeg")
                    else:
                        self._send(404, b"no screenshot", "text/plain")
                    return
                if path == "/events":
                    p = run_dir / "events.jsonl"
                    lines = p.read_text(encoding="utf-8").splitlines()[-8:] if p.exists() else []
                    self._send(200, json.dumps([json.loads(line) for line in lines]).encode())
                    return
                self._send(404, b"not found", "text/plain")

            def do_POST(self) -> None:  # noqa: N802
                path = urlsplit(self.path).path
                parts = [p for p in path.split("/") if p]
                if len(parts) != 3 or parts[0] != "interventions":
                    self._send(404, b"not found", "text/plain")
                    return
                _, intervention_id, command = parts
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                ctype = self.headers.get("Content-Type", "")
                data: dict[str, str] = {}
                if "json" in ctype and raw:
                    data = {k: str(v) for k, v in json.loads(raw).items()}
                elif raw:
                    data = {k: v[0] for k, v in parse_qs(raw).items()}
                pending = read_intervention(run_dir) or {}
                if pending.get("intervention_id") != intervention_id or pending.get("released_at"):
                    self._send(HTTPStatus.CONFLICT, json.dumps({"error": "stale or unknown intervention id"}).encode())
                    return
                mode = data.get("mode")
                if command == "resume" and mode not in RESUME_MODES:
                    self._send(400, json.dumps({"error": f"mode must be one of {RESUME_MODES}"}).encode())
                    return
                try:
                    cmd = append_command(run_dir, intervention_id=intervention_id, command=command, operator=data.get("operator") or "operator", mode=mode, note=data.get("note"))
                except ValueError as e:
                    self._send(400, json.dumps({"error": str(e)}).encode())
                    return
                self._send(200, json.dumps(cmd).encode())

        for port in range(self.port, self.port + 20):
            try:
                self._httpd = ThreadingHTTPServer((self.host, port), Handler)
                self.port = port
                break
            except OSError:
                continue
        if self._httpd is None:
            raise RuntimeError("no free port for the operator console")
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="teller-operator", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
