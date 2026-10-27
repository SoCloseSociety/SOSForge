FROM python:3.12-slim

# FORWARDED_ALLOW_IPS: which peers uvicorn believes when they say who the
# client is (X-Forwarded-For). Its default is 127.0.0.1 only, and the API is
# reached from the web container over the compose network, so every visitor
# arrived as that container's address: the per-address websocket cap
# (max_ws_per_ip, 24) was a cap on the WHOLE SITE. Trusted here: loopback and
# the ranges Docker allocates bridge networks from by default (production's
# compose network is 172.28.0.0/16, measured 2026-10-26; it is not pinned in
# docker-compose.yml, so it can change on a recreate). Never "*": with it, any
# peer could choose its own address. The API publishes no port, so only the
# compose network reaches it at all. Override per deployment if the compose
# network is ever pinned.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FORWARDED_ALLOW_IPS="127.0.0.1,::1,172.16.0.0/12,192.168.0.0/16"

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

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300", "--proxy-headers"]
