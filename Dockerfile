FROM python:3.12-slim

WORKDIR /app
COPY lark_proxy.py .

# Fly.io injects PORT at runtime; EXPOSE is documentation only
EXPOSE 8080

CMD ["python", "lark_proxy.py"]
