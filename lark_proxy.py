#!/usr/bin/env python3
"""
Minimal CORS proxy for the Lark API Console — Fly.io ready.

Usage locally:
    python lark_proxy.py            # listens on http://localhost:8000
    python lark_proxy.py 9000       # custom port

On Fly.io:
    Reads PORT from the environment (Fly sets this) and binds 0.0.0.0
    so the app is reachable. See Dockerfile / fly.toml alongside this file.

The HTML page calls:  POST {proxy}/proxy?url=<lark_api_url>
This forwards the request (body + Authorization header) to Lark and
returns the response with permissive CORS headers added.

Only stdlib — no pip install needed.

Optional: set APP_ID / APP_SECRET as Fly secrets to let the proxy
mint its own app_access_token server-side via GET /token, so the
frontend never needs to handle app_secret directly.
"""
import os
import sys
import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ALLOWED_HOST = "open.larksuite.com"  # only proxy to Lark
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        # Health check — Fly.io pings this to confirm the app is alive
        if parsed.path == "/health":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        # Optional: server-side token mint, using secrets set on Fly
        # (fly secrets set APP_ID=... APP_SECRET=...)
        if parsed.path == "/token":
            if not APP_ID or not APP_SECRET:
                self.send_response(400)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"APP_ID/APP_SECRET not set on server"}')
                return

            body = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
            req = urllib.request.Request(
                f"https://{ALLOWED_HOST}/open-apis/auth/v3/app_access_token/internal",
                data=body,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status, data = resp.status, resp.read()
            except urllib.error.HTTPError as e:
                status, data = e.code, e.read()
            except Exception as e:
                status, data = 502, json.dumps({"error": str(e)}).encode()

            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self._cors()
        self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/proxy":
            self.send_response(404)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"error":"use /proxy?url=..."}')
            return

        target = parse_qs(parsed.query).get("url", [None])[0]
        if not target or urlparse(target).hostname != ALLOWED_HOST:
            self.send_response(400)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"missing or disallowed url"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"

        req = urllib.request.Request(target, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        auth = self.headers.get("Authorization")
        if auth:
            req.add_header("Authorization", auth)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, data = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, data = e.code, e.read()
        except Exception as e:
            status = 502
            data = json.dumps({"error": str(e)}).encode()

        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    # Fly.io sets PORT; fall back to a CLI arg or 8000 for local use
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8000))
    host = "0.0.0.0"  # must bind all interfaces, not just localhost, for Fly.io
    print(f"Lark proxy running at http://{host}:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()

        parsed = urlparse(self.path)
        if parsed.path != "/proxy":
            self.send_response(404)
            self._cors()
            self.end_headers()
            self.wfile.write(b'{"error":"use /proxy?url=..."}')
            return

        target = parse_qs(parsed.query).get("url", [None])[0]
        if not target or urlparse(target).hostname != ALLOWED_HOST:
            self.send_response(400)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"missing or disallowed url"}')
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"

        req = urllib.request.Request(target, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        auth = self.headers.get("Authorization")
        if auth:
            req.add_header("Authorization", auth)

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, data = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, data = e.code, e.read()
        except Exception as e:
            status = 502
            data = json.dumps({"error": str(e)}).encode()

        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # quiet


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Lark proxy running at http://localhost:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
