FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" \
    "pydantic-settings>=2.6" "httpx>=0.27" "websockets>=13.0"

COPY app ./app

RUN useradd --create-home --uid 10001 sos && mkdir -p /srv/data && chown -R sos /srv
USER sos

EXPOSE 8300
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8300/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
