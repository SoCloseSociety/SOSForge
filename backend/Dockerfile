FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies come from pyproject.toml, not from a list copied by hand into
# this file: a second list is a second thing to forget to update, and it had
# already drifted.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 sos && mkdir -p /srv/data && chown -R sos /srv
USER sos

EXPOSE 8300
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8300/healthz')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
