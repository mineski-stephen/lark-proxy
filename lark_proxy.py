#!/usr/bin/env python3
"""
Minimal CORS proxy for the Lark API Console — Render ready.

Why this exists
---------------
Browsers refuse to let the static ``index.html`` page call the Lark Open API
directly: Lark does not return permissive CORS headers, so the request is
blocked. This tiny standard-library HTTP server sits in the middle. The page
POSTs to ``/proxy?url=<lark_api_url>`` and the proxy forwards the request
(body + Authorization header) to Lark, then relays Lark's response back with
permissive CORS headers attached so the browser accepts it.

    browser (index.html)  ──POST /proxy?url=…──▶  this proxy  ──▶  Lark API
                          ◀──── response + CORS ───────────────◀──

Running locally
---------------
    python lark_proxy.py            # listens on http://localhost:8000
    python lark_proxy.py 9000       # custom port

Running on Render
-----------------
Render injects the ``PORT`` environment variable and routes traffic to
whatever port the service binds. This script reads ``PORT`` and binds
``0.0.0.0:$PORT`` so the platform can reach it. Point Render's health check
at ``/health`` (or leave the default ``/`` — both return 200). See the
Dockerfile and README.md alongside this file for full deploy steps.

Endpoints
---------
    GET  /          -> service info JSON (handy browser sanity check)
    GET  /health    -> {"status":"ok"}                  (health check)
    GET  /token     -> mints an app_access_token server-side from the
                       APP_ID / APP_SECRET env vars (optional convenience)
    POST /proxy?url=<lark_url>
                    -> forwards the request to <lark_url> and relays the reply
    OPTIONS *       -> CORS preflight (204)

Configuration — all optional, via environment variables
--------------------------------------------------------
    PORT             Port to bind (Render sets this; 8000 locally if unset)
    ALLOWED_HOSTS    Comma-separated hostnames /proxy may forward to
                     (default "open.larksuite.com,open.feishu.cn")
    LARK_HOST        Host used by /token (default "open.larksuite.com")
    ALLOWED_ORIGIN   Value for Access-Control-Allow-Origin (default "*")
    APP_ID           Lark app id     — only needed for the /token convenience
    APP_SECRET       Lark app secret — only needed for the /token convenience
    UPSTREAM_TIMEOUT Seconds to wait for Lark (default 30)
    MAX_BODY_BYTES   Largest request body /proxy will accept (default 10 MiB)

Only the Python standard library is used — no ``pip install`` required.
"""
import os
import sys
import json
import signal
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def _host_set(name, default):
    """Parse a comma-separated env var into a lowercased set of hostnames."""
    raw = os.environ.get(name, default)
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


ALLOWED_HOSTS = _host_set("ALLOWED_HOSTS", "open.larksuite.com,open.feishu.cn")
LARK_HOST = os.environ.get("LARK_HOST", "open.larksuite.com").strip().lower()
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
APP_ID = os.environ.get("APP_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "30"))
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(10 * 1024 * 1024)))


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 keeps connections alive between requests. Every response below
    # sends a Content-Length (or is a body-less status) so framing stays valid.
    protocol_version = "HTTP/1.1"
    server_version = "LarkProxy/1.0"
    # Drop idle keep-alive / slow-client connections instead of pinning a
    # worker thread forever.
    timeout = 65

    # ---- response helpers -------------------------------------------------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")  # cache preflight 24h

    def _send(self, status, body=b"", content_type="application/json"):
        """Send a fully-framed response with CORS headers."""
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self._cors()
        # 204/304 carry no body and (per RFC 7230) no Content-Length.
        if status not in (204, 304):
            if body:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and status not in (204, 304):
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), "application/json")

    # ---- routes -----------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204)  # CORS preflight

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._json(200, {"status": "ok"})
        elif path == "/":
            self._json(200, {
                "service": "lark-cors-proxy",
                "endpoints": ["/health", "/token", "/proxy?url=<lark_url>"],
                "allowed_hosts": sorted(ALLOWED_HOSTS),
            })
        elif path == "/token":
            self._mint_token()
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/proxy":
            self._json(404, {"error": "use POST /proxy?url=..."})
            return

        # Validate the target before touching it: https only, host allowlisted.
        target = parse_qs(parsed.query).get("url", [None])[0]
        tparsed = urlparse(target) if target else None
        if (not tparsed or tparsed.scheme != "https"
                or (tparsed.hostname or "").lower() not in ALLOWED_HOSTS):
            self._json(400, {
                "error": "missing or disallowed url (https + allowed host required)",
                "allowed_hosts": sorted(ALLOWED_HOSTS),
            })
            return

        # Read the request body, guarding against oversized payloads.
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length > MAX_BODY_BYTES:
            self._json(413, {"error": "request body too large"})
            return
        body = self.rfile.read(length) if length else b"{}"

        # Forward to Lark, relaying content type and any Authorization header.
        req = urllib.request.Request(target, data=body, method="POST")
        req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        auth = self.headers.get("Authorization")
        if auth:
            req.add_header("Authorization", auth)

        status, data, ctype = self._fetch(req)
        self._send(status, data, ctype)

    # ---- upstream calls ---------------------------------------------------
    def _mint_token(self):
        """Mint an app_access_token using server-side APP_ID / APP_SECRET."""
        if not APP_ID or not APP_SECRET:
            self._json(400, {"error": "APP_ID/APP_SECRET not set on server"})
            return
        body = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
        req = urllib.request.Request(
            f"https://{LARK_HOST}/open-apis/auth/v3/app_access_token/internal",
            data=body,
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        status, data, ctype = self._fetch(req)
        self._send(status, data, ctype)

    @staticmethod
    def _fetch(req):
        """Run an upstream request; return (status, body_bytes, content_type)."""
        try:
            with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as resp:
                return resp.status, resp.read(), resp.headers.get_content_type()
        except urllib.error.HTTPError as e:
            ctype = e.headers.get_content_type() if e.headers else "application/json"
            return e.code, e.read(), ctype
        except Exception as e:  # network error, timeout, DNS, etc.
            return 502, json.dumps({"error": str(e)}).encode(), "application/json"

    # ---- logging ----------------------------------------------------------
    def log_request(self, code="-", size="-"):
        # Default logging echoes the full request line including ?url=<lark_url>.
        # Strip the query string so resource IDs stay out of the logs; bodies
        # and the Authorization header are never logged.
        self.log_message('"%s %s" %s', self.command,
                         self.path.split("?", 1)[0], str(code))


def main():
    # Render injects PORT; fall back to a CLI arg, then 8000, for local use.
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else "8000"))
    host = "0.0.0.0"  # bind all interfaces so the platform can route to us
    httpd = ThreadingHTTPServer((host, port), Handler)

    # Render sends SIGTERM on deploy/restart. Re-raise it as KeyboardInterrupt
    # so serve_forever() unwinds cleanly (calling httpd.shutdown() from a
    # signal handler running in this same thread would deadlock).
    def _on_sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _on_sigterm)

    print(f"Lark proxy listening on http://{host}:{port}  (Ctrl+C to stop)", flush=True)
    print(f"  allowed hosts: {', '.join(sorted(ALLOWED_HOSTS))}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down…", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
