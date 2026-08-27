"""Cleanroom — Local Dashboard

Stdlib-only HTTP server serving a single-page app plus a JSON API.

Loopback-only by design; refuses any non-local Host header.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

import argparse
import errno
import ipaddress
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import runner
from .payload import build_dashboard_payload, build_settings_payload

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_REQUEST_BYTES = 256_000
MAX_UPLOAD_BYTES = 110_000_000  # base64 headroom for ~100MB files


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CleanerDashboard/1.0"

    # ── security helpers ──

    def _is_loopback_bind_host(self, host: str) -> bool:
        return host in ("localhost", "127.0.0.1", "::1")

    def _loopback_netloc(self, netloc: str) -> bool:
        try:
            salida = urlparse(f"//{netloc}")
            hostname = salida.hostname
            _ = salida.port
        except ValueError:
            return False
        if not hostname:
            return False
        if hostname.lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            return False

    def _local_request_allowed(self) -> bool:
        try:
            peer = ipaddress.ip_address(self.client_address[0])
        except (ValueError, IndexError):
            return False
        if not peer.is_loopback:
            return False
        host = self.headers.get("Host", "").strip().lower()
        if not host or not self._loopback_netloc(host):
            return False
        return True

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("invalid body length")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # ── routing ──

    def do_GET(self) -> None:
        if not self._local_request_allowed():
            self._send_json({"error": "local only"}, 403)
            return
        salida = urlparse(self.path)
        route = salida.path
        if route in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif route == "/favicon.svg":
            self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml")
        # ── I added this: serve the persona-avatar widget script ─────────
        # I need the avatar library reachable by the browser; without this
        # route the server's whitelist would 404 it.
        elif route == "/persona-avatar.js":
            self._send_file(STATIC_DIR / "persona-avatar.js", "text/javascript; charset=utf-8")
        # ── end of my persona-avatar route ──
        elif route == "/api/payload":
            self._send_json(build_dashboard_payload())
        elif route == "/api/settings":
            from ..config import get_settings
            self._send_json(build_settings_payload(get_settings()))
        elif route == "/api/jobs":
            self._send_json({"jobs": runner.list_jobs(), "busy": runner.busy()})
        elif route.startswith("/api/jobs/"):
            job_id = route.rsplit("/", 1)[-1]
            job = runner.get_job(job_id)
            if job is None:
                self._send_json({"error": "job not found"}, 404)
            else:
                self._send_json({"job": job})
        elif route == "/api/cost":
            from ..config import get_settings
            from ..telemetry import build_cost_summary
            self._send_json({"cost": build_cost_summary(get_settings().telemetry_path)})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._local_request_allowed():
            self._send_json({"error": "local only"}, 403)
            return
        salida = urlparse(self.path)
        route = salida.path
        if route == "/api/run":
            self._api_run()
        else:
            self._send_json({"error": "not found"}, 404)

    # ── handlers ──

    def _api_run(self) -> None:
        try:
            data = self._read_json_body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": f"invalid request: {exc}"}, 400)
            return
        filename = str(data.get("filename") or "input.txt")
        b64 = str(data.get("content") or "")
        strength = str(data.get("strength") or "safe")
        apply = bool(data.get("apply", True))
        if len(b64) > MAX_UPLOAD_BYTES:
            self._send_json({"error": "upload too large"}, 413)
            return
        job_id, err = runner.start_clean_job(filename, b64, strength=strength, apply=apply)
        if err:
            self._send_json({"error": err}, 409)
            return
        self._send_json({"job_id": job_id, "status": "running"})

    # ── plumbing ──

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cleanroom dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if args.host not in ("localhost", "127.0.0.1", "::1"):
        parser.error("--host must be a loopback address; the dashboard is local-only")

    try:
        server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {args.port} already in use — a dashboard is likely running at http://{args.host}:{args.port}")
            return 1
        raise
    url = f"http://{args.host}:{args.port}"
    print(f"Cleanroom dashboard → {url}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())