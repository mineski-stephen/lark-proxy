# syntax=docker/dockerfile:1
#
# Container image for the Lark CORS proxy, built for Render.
# The proxy is a single stdlib-only Python file, so there is no
# requirements.txt and no build step — just copy and run.

FROM python:3.12-slim

# PYTHONUNBUFFERED  -> stdout/stderr stream straight to Render's log viewer
# PYTHONDONTWRITEBYTECODE -> no stray .pyc files in the image
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy the proxy. (.dockerignore keeps everything else out of the build.)
COPY lark_proxy.py .

# Run as an unprivileged user — never as root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Render injects PORT at runtime (default 10000) and routes traffic to it;
# the app binds 0.0.0.0:$PORT regardless. EXPOSE is documentation only.
EXPOSE 10000

# Local-only liveness probe. Render uses its own health check (configure it
# to hit /health in the dashboard or render.yaml) and ignores this directive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4).status==200 else 1)"

CMD ["python", "lark_proxy.py"]
