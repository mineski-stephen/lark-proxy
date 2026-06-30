# Lark CORS Proxy (`lark_proxy.py`)

A tiny, dependency-free HTTP proxy that lets the **Lark API Console**
(`../index.html`) talk to the [Lark / Feishu Open API](https://open.larksuite.com)
from the browser.

It's written in pure Python standard library (no `pip install`), runs locally
with one command, and ships with a Dockerfile so it can be deployed to
[Render](https://render.com) as a web service.

---

## Why this proxy exists

The console is a static HTML page. When JavaScript in the browser tries to
`fetch()` the Lark API directly, the browser blocks the response because Lark
doesn't send permissive **CORS** (Cross-Origin Resource Sharing) headers. The
request actually reaches Lark, but the browser refuses to hand the response
back to the page.

This proxy sits in the middle. The browser calls the proxy (same-origin or
CORS-friendly), the proxy calls Lark **server-to-server** (no CORS rules apply
between servers), and then the proxy returns Lark's response to the browser
**with** the CORS headers the browser needs.

```
   ┌────────────────────┐   POST /proxy?url=<lark_url>   ┌──────────────┐   HTTPS    ┌────────────┐
   │  Browser           │ ─────────────────────────────▶ │  lark_proxy  │ ─────────▶ │  Lark API  │
   │  (index.html)      │ ◀───────────────────────────── │  (this app)  │ ◀───────── │            │
   └────────────────────┘   response + CORS headers       └──────────────┘  response  └────────────┘
```

The proxy is deliberately small and locked down: it only forwards `POST`
requests, only to an allow-list of Lark hosts, and only over HTTPS.

---

## Endpoints

| Method   | Path                       | Purpose                                                                 |
| -------- | -------------------------- | ----------------------------------------------------------------------- |
| `GET`    | `/`                        | Service info JSON. Useful for a quick browser sanity check.             |
| `GET`    | `/health`                  | Liveness probe — returns `{"status":"ok"}`. Point Render's health check here. |
| `GET`    | `/token`                   | Mints an `app_access_token` **server-side** (see [Server-side token minting](#optional-server-side-token-minting)). Requires `APP_ID` / `APP_SECRET` env vars. |
| `POST`   | `/proxy?url=<lark_url>`    | The main route. Forwards the request body + `Authorization` header to `<lark_url>` and relays the response. |
| `OPTIONS`| *(any)*                    | CORS preflight — returns `204` with the CORS headers.                  |

### `POST /proxy` rules

The `url` query parameter (URL-encoded) is validated before anything is
forwarded. The request is rejected with `400` unless **all** of these hold:

- the scheme is `https`, and
- the host is in the [`ALLOWED_HOSTS`](#configuration) allow-list.

The body is read (capped at `MAX_BODY_BYTES`), the incoming `Content-Type` and
`Authorization` headers are forwarded, and Lark's status code, body, and
content type are relayed back verbatim. Network failures/timeouts return
`502` with a JSON error.

---

## Configuration

Everything is configurable through environment variables; every one has a
sensible default, so the proxy runs with **zero** configuration.

| Variable           | Default                                  | Description                                                                 |
| ------------------ | ---------------------------------------- | --------------------------------------------------------------------------- |
| `PORT`             | `8000` (local)                           | Port to bind. **Render sets this automatically** (default `10000`).         |
| `ALLOWED_HOSTS`    | `open.larksuite.com,open.feishu.cn`      | Comma-separated hostnames `/proxy` may forward to. Use `open.larksuite.com` for Lark (international) and `open.feishu.cn` for Feishu (China). |
| `LARK_HOST`        | `open.larksuite.com`                     | Host used by the `/token` endpoint.                                         |
| `ALLOWED_ORIGIN`   | `*`                                      | Value sent in `Access-Control-Allow-Origin`. Set to a specific origin to lock the proxy to your page. |
| `APP_ID`           | *(empty)*                                | Lark app id — only needed for `/token`.                                     |
| `APP_SECRET`       | *(empty)*                                | Lark app secret — only needed for `/token`.                                 |
| `UPSTREAM_TIMEOUT` | `30`                                     | Seconds to wait for Lark before giving up (`502`).                          |
| `MAX_BODY_BYTES`   | `10485760` (10 MiB)                      | Largest request body `/proxy` accepts before returning `413`.               |

---

## Running locally

Requires Python 3.8+ (tested on 3.12 / 3.13). No dependencies.

```bash
cd py
python lark_proxy.py            # http://localhost:8000
python lark_proxy.py 9000       # custom port
```

Quick check:

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

Then open `../index.html` in your browser and set **Proxy base URL** to
`http://localhost:8000` (see [Connecting the console](#connecting-the-console)).

### Running locally with Docker

```bash
cd py
docker build -t lark-proxy .
docker run --rm -p 8000:8000 lark-proxy
# proxy is now on http://localhost:8000
```

The container binds the port given by `PORT` (default `8000`). To run on a
different port, pass it through:

```bash
docker run --rm -e PORT=9000 -p 9000:9000 lark-proxy
```

---

## Deploying to Render

The proxy is built to run as a Render **Web Service** from this Dockerfile.

### Option A — Render dashboard (manual)

1. Push this repository to GitHub/GitLab.
2. In Render, click **New ➜ Web Service** and connect the repo.
3. Configure:
   - **Runtime:** `Docker`
   - **Root Directory:** `py` *(this is where the Dockerfile lives)*
   - **Health Check Path:** `/health`
   - **Instance Type:** Free or Starter is plenty.
4. *(Optional)* Under **Environment**, add `APP_ID` and `APP_SECRET` if you
   want the server-side `/token` endpoint. Add any other variable from the
   [Configuration](#configuration) table as needed.
5. Click **Create Web Service**. Render builds the image, starts the
   container, and gives you a URL like `https://lark-proxy.onrender.com`.

You do **not** need to set `PORT` — Render injects it and the proxy binds to
it automatically.

### Option B — `render.yaml` Blueprint (infrastructure as code)

Commit a `render.yaml` at the **repository root** to deploy via Render
Blueprints:

```yaml
services:
  - type: web
    name: lark-proxy
    runtime: docker
    rootDir: py
    dockerfilePath: ./Dockerfile
    healthCheckPath: /health
    plan: free
    envVars:
      - key: ALLOWED_ORIGIN
        value: "*"
      # Uncomment to enable the server-side /token endpoint.
      # Mark these as secret in the Render dashboard rather than committing them.
      # - key: APP_ID
      #   sync: false
      # - key: APP_SECRET
      #   sync: false
```

Then in Render: **New ➜ Blueprint**, point it at the repo, and apply.

### After deploying

Render free instances **spin down when idle** and cold-start on the next
request (a few seconds of delay) — fine for a testing tool. Visit
`https://<your-service>.onrender.com/` to confirm it's up; you'll get the
service-info JSON.

---

## Connecting the console

In `../index.html`, set the **Proxy base URL** field to your proxy's address:

- Local: `http://localhost:8000`
- Render: `https://<your-service>.onrender.com`

The page rewrites its Lark calls to `"<proxy>/proxy?url=" + encodeURIComponent(<lark_url>)`,
so all traffic flows through the proxy. Leaving the field blank makes the page
call Lark directly — which the browser will block via CORS.

---

## Optional: server-side token minting

By default the console sends `app_id` / `app_secret` through `/proxy` to mint a
token, which means those secrets travel from the browser. If you'd rather keep
them on the server, set `APP_ID` and `APP_SECRET` as Render environment
variables and call the proxy's own `/token` endpoint instead:

```bash
curl https://<your-service>.onrender.com/token
# relays Lark's app_access_token response — secrets never leave the server
```

This endpoint reads the credentials from the environment, calls
`https://<LARK_HOST>/open-apis/auth/v3/app_access_token/internal`, and relays
the result. The console doesn't use it out of the box, but it's there if you
want to wire it up.

---

## Security model

- **Host allow-list.** `/proxy` only forwards to hosts in `ALLOWED_HOSTS`. An
  attacker can't use it as an open proxy or to reach internal services (SSRF).
- **HTTPS only.** Non-`https` targets are rejected.
- **Method-limited.** Only `GET` (info/health/token) and `POST` (`/proxy`) do
  anything; everything else is `404`.
- **Body cap.** Requests larger than `MAX_BODY_BYTES` are rejected with `413`.
- **Secrets stay out of logs.** The access log records only method, path
  (without the query string), and status — never request bodies or the
  `Authorization` header. The `?url=…` query is stripped from log lines so
  Lark resource IDs aren't recorded.
- **Permissive CORS by default.** `Access-Control-Allow-Origin` is `*` so any
  page can use it. For a locked-down deployment, set `ALLOWED_ORIGIN` to your
  console's exact origin.

> This is a developer/testing tool. It performs no authentication of its own —
> anyone who can reach the URL can use it to call the allow-listed Lark hosts
> (they still need valid Lark credentials for any non-public Lark endpoint).
> Don't treat the public Render URL as private.

---

## Logging

Logs go to stdout/stderr, which Render captures in its **Logs** tab. On
startup the proxy prints the bind address and the active host allow-list, then
one concise line per request:

```
Lark proxy listening on http://0.0.0.0:10000  (Ctrl+C to stop)
  allowed hosts: open.feishu.cn, open.larksuite.com
127.0.0.1 - - [30/Jun/2026 15:33:06] "GET /health" 200
127.0.0.1 - - [30/Jun/2026 15:33:33] "POST /proxy" 200
```

---

## Troubleshooting

| Symptom                                              | Likely cause / fix                                                                                   |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Render shows **"No open ports detected"**            | The service must listen on Render's `PORT`. This proxy does automatically — make sure you didn't override `PORT` with a value the platform isn't routing to. |
| Console shows a **CORS error** even with the proxy   | The **Proxy base URL** field is blank or wrong, so the page is calling Lark directly. Set it to the proxy URL. |
| `400 missing or disallowed url`                      | The target isn't `https`, or its host isn't in `ALLOWED_HOSTS`. Add the host via the env var if it's a legitimate Lark domain. |
| `502` with `{"error": "..."}`                        | The proxy couldn't reach Lark (network/DNS/timeout). Check the error message; raise `UPSTREAM_TIMEOUT` if Lark is just slow. |
| `400 APP_ID/APP_SECRET not set on server`            | You hit `/token` without setting those env vars. Set them, or use `/proxy` instead.                 |
| First request after idle is slow                     | Render free tier cold start. Expected; upgrade the plan or ping `/health` periodically to keep it warm. |

---

## Files

| File             | Purpose                                            |
| ---------------- | -------------------------------------------------- |
| `lark_proxy.py`  | The proxy server (stdlib only).                    |
| `Dockerfile`     | Container image for Render (and local Docker).     |
| `.dockerignore`  | Keeps docs/cruft out of the built image.           |
| `README.md`      | This file.                                         |
