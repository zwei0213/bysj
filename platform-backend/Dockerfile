FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    UVICORN_WORKERS=2

WORKDIR /srv/platform-backend

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -m -s /usr/sbin/nologin appuser && \
    mkdir -p /srv/platform-backend /srv/data /srv/storage && \
    chown -R appuser:appgroup /srv/platform-backend /srv/data /srv/storage

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup app ./app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-2} --proxy-headers --forwarded-allow-ips='*'"]
