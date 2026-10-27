"""The /download/ routes of deploy/nginx.conf, read from the file itself.

nginx is not installed on the development Mac and docker is not available
there, so these tests do NOT prove the server behaves: `curl -I` on the
deployed container does (lesson 23). What they pin is the shape the routes
must keep, and they resolve each URL the way nginx documents it (exact match,
then the longest prefix, then the regexes in file order unless the prefix is
`^~`), so a new location that silently captures /download/ fails here.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NGINX = (ROOT / "deploy" / "nginx.conf").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def blocks(text: str, keyword: str) -> list[tuple[str, str]]:
    """(header, body) of every `keyword ... { ... }` block, braces balanced."""
    found = []
    for m in re.finditer(rf"(?m)^\s*{keyword}\s+([^{{]*)\{{", text):
        depth, i = 0, m.end() - 1
        for j in range(i, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    found.append((m.group(1).strip(), text[i + 1 : j]))
                    break
    return found


CONF = strip_comments(NGINX)
LOCATIONS = blocks(CONF, "location")


EMPTY_TYPES = re.compile(r"types\s*\{\s*\}")


def directives(body: str) -> list[str]:
    """Simple directives; an empty `types { }` block is reported as
    `types {}` (it has no semicolon of its own)."""
    out = ["types {}"] if EMPTY_TYPES.search(body) else []
    body = EMPTY_TYPES.sub("", body)
    # split on ';' outside quotes: the CSP value is full of them
    parts, current, quote = [], [], ""
    for ch in body:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == ";":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return out + [" ".join(s.split()) for s in parts if s.strip()]


def resolve(uri: str) -> tuple[str, str]:
    """nginx's location selection, for the location kinds this file uses."""
    exact, prefixes, regexes = {}, [], []
    for header, body in LOCATIONS:
        parts = header.split(None, 1)
        if parts[0] == "=":
            exact[parts[1]] = (header, body)
        elif parts[0] in ("~", "~*"):
            flags = re.I if parts[0] == "~*" else 0
            regexes.append((re.compile(parts[1], flags), (header, body)))
        elif parts[0] == "^~":
            prefixes.append((parts[1], True, (header, body)))
        else:
            prefixes.append((parts[0], False, (header, body)))
    if uri in exact:
        return exact[uri]
    best = max((p for p in prefixes if uri.startswith(p[0])), key=lambda p: len(p[0]), default=None)
    if best and best[1]:
        return best[2]
    for rx, block in regexes:
        if rx.search(uri):
            return block
    assert best, f"nothing serves {uri}"
    return best[2]


def map_value(variable: str, uri: str) -> str:
    """The value of an nginx `map $uri $variable` for one URI: exact keys
    first, then regexes in order, then default."""
    body = next(b for h, b in blocks(CONF, "map") if h.split()[-1] == variable)
    exact, regexes, default = {}, [], ""
    for line in body.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0], parts[1].rstrip(";").strip().strip('"')
        if key == "default":
            default = value
        elif key.startswith("~"):
            regexes.append((re.compile(key[1:]), value))
        else:
            exact[key] = value
    if uri in exact:
        return exact[uri]
    return next((v for rx, v in regexes if rx.search(uri)), default)


def load_publisher():
    spec = importlib.util.spec_from_file_location("publish_apk", ROOT / "tools" / "publish_apk.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_apk"] = module
    spec.loader.exec_module(module)
    return module


APK = "application/vnd.android.package-archive"


@pytest.mark.parametrize(
    ("uri", "alias", "content_type"),
    [
        ("/download/latest.json", "/srv/downloads/latest.json", "application/json"),
        ("/download/sosforge-v0.1.1.apk", "/srv/downloads/$1", APK),
        ("/download/sosforge-v0.1.12-rc1.apk", "/srv/downloads/$1", APK),
        ("/download/sosforge-latest.apk", "/srv/downloads/$1", APK),
        ("/download/sosforge-v0.1.1.apk.sha256", "/srv/downloads/$1", "text/plain"),
    ],
)
def test_release_files_come_from_the_mount_with_their_own_type(uri, alias, content_type):
    header, body = resolve(uri)
    d = directives(body)
    assert f"alias {alias}" in d, header
    # the type is set HERE, with an empty table: a server-level `types`
    # block would replace the MIME table of every other file (lesson 23)
    assert "types {}" in d
    assert f"default_type {content_type}" in d
    assert not any(x.startswith("try_files") for x in d), "a fallback would hide a missing file"


@pytest.mark.parametrize(
    "uri",
    [
        "/download/",
        "/download/missing.apk",
        "/download/README.md",
        "/download/.gitignore",
        "/download/sosforge-v0.1.1.apk.tmp",
    ],
)
def test_nothing_else_under_download_falls_back_to_the_app(uri):
    header, body = resolve(uri)
    assert header == "/download/"
    assert directives(body) == ["try_files $uri $uri/ =404"]


def test_download_without_the_slash_redirects_relatively():
    header, body = resolve("/download")
    d = directives(body)
    assert "return 301 /download/" in d
    # The container listens on plain :80 behind the host's TLS: an absolute
    # redirect would send the phone to http://.
    assert "absolute_redirect off" in d


def test_the_app_routes_are_untouched():
    assert resolve("/")[0] == "/"
    assert resolve("/fr/")[0] == "/"
    assert resolve("/assets/index-abc.js")[0] == "/assets/"
    assert resolve("/api/events")[0] == "/api/"
    assert resolve("/ws")[0] == "/ws"


@pytest.mark.parametrize(
    ("uri", "cache"),
    [
        ("/download/sosforge-v0.1.1.apk", "public, max-age=31536000, immutable"),
        ("/download/sosforge-v0.1.1.apk.sha256", "public, max-age=31536000, immutable"),
        ("/download/latest.json", "no-cache"),
        ("/download/sosforge-latest.apk", "no-cache"),
        ("/download/index.html", "no-cache"),
        ("/download/download.js", "no-cache"),
        ("/sw.js", "no-cache"),
        ("/index.html", "no-cache"),
    ],
)
def test_cache_policy(uri, cache):
    assert map_value("$sos_cache_control", uri) == cache


@pytest.mark.parametrize(
    ("uri", "disposition"),
    [
        ("/download/sosforge-v0.1.1.apk", "attachment"),
        ("/download/sosforge-latest.apk", "attachment"),
        ("/download/sosforge-v0.1.1.apk.sha256", ""),
        ("/download/latest.json", ""),
        ("/download/", ""),
        ("/", ""),
    ],
)
def test_only_the_apk_is_an_attachment(uri, disposition):
    assert map_value("$sos_content_disposition", uri) == disposition


def test_every_add_header_is_at_server_level():
    """An add_header inside a location drops every inherited one (the CSP
    included): the file's own rule, kept true by the download block too."""
    for header, body in LOCATIONS:
        assert "add_header" not in body, header
    server = server_directives()
    assert "add_header Content-Disposition $sos_content_disposition" in server
    assert "add_header Cache-Control $sos_cache_control" in server


def server_directives() -> list[str]:
    """The server block's own directives, locations removed."""
    body = blocks(CONF, "server")[0][1]
    for _, location in LOCATIONS:
        body = body.replace(location, "")
    return directives(body)


def test_what_describes_the_file_is_not_sent_on_an_error_page():
    """A 404 for an APK not uploaded yet must not be cacheable for a year,
    nor offered as an attachment: no `always` on those two. The security
    headers keep it -- an error page needs its CSP as much as any page."""
    server = server_directives()
    for header in ("Cache-Control", "Content-Disposition"):
        line = next(d for d in server if d.startswith(f"add_header {header} "))
        assert not line.endswith(" always"), line
    for header in ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy"):
        line = next(d for d in server if d.startswith(f"add_header {header} "))
        assert line.endswith(" always"), line


def prerendered_languages() -> list[str]:
    """What scripts/prerender-langs.mjs writes: one shell per key of seo.json."""
    seo = json.loads((ROOT / "frontend" / "src" / "seo.json").read_text())
    return sorted(seo)


@pytest.mark.parametrize("uri", ["/index.html", "/download/index.html", "/download/"])
def test_the_main_shells_are_revalidated(uri):
    assert map_value("$sos_cache_control", uri) == "no-cache"


def test_every_language_shell_is_revalidated():
    """/fr/ is served as /fr/index.html after the index redirect; it was
    missing from the map and production sent it with no Cache-Control."""
    langs = [lang for lang in prerendered_languages() if lang != "en"]
    assert {"fr", "es", "ja", "id"} <= set(langs)
    for lang in langs:
        for uri in (f"/{lang}/index.html", f"/{lang}/"):
            assert map_value("$sos_cache_control", uri) == "no-cache", uri
    # and the generic shapes do not swallow what must stay long-cached
    assert (
        map_value("$sos_cache_control", "/assets/index-abc.js")
        == "public, max-age=31536000, immutable"
    )
    assert (
        map_value("$sos_cache_control", "/download/sosforge-v0.1.1.apk")
        == "public, max-age=31536000, immutable"
    )


PLACE_CARRIERS = (
    "$request ",
    '$request"',
    "$request_uri",
    "$args",
    "$query_string",
    "$arg_",
    "$http_x_forwarded_for",
    "$proxy_add_x_forwarded_for",
    "$http_referer",
)


def test_the_access_log_holds_no_query_string_and_no_visitor_address():
    """/ws?zones=lat,lon,r and /api/geocode?q=<a street> are someone's
    places; the stock `main` format logged them next to X-Forwarded-For."""
    match = re.search(r"log_format\s+sos_private\s+((?:'[^']*'\s*)+);", CONF)
    assert match, "no sos_private log_format"
    fmt = "".join(re.findall(r"'([^']*)'", match.group(1)))
    for carrier in PLACE_CARRIERS:
        assert carrier not in fmt + " ", carrier
    assert "$uri$is_args" in fmt
    logs = [d for d in server_directives() if d.startswith("access_log")]
    assert logs == ["access_log /var/log/nginx/access.log sos_private"]
    assert not any("access_log" in body for _, body in LOCATIONS)


def test_the_web_container_log_is_bounded():
    web = COMPOSE[COMPOSE.index("\n  web:") :]
    assert "driver: json-file" in web
    assert re.search(r"max-size: '\d+m'", web)
    assert re.search(r"max-file: '\d+'", web)


def test_every_name_the_publisher_writes_is_routed():
    publisher = load_publisher()
    for version in ("0.1.1", "1.0.0", "0.2.0-rc1", "2.0.0_b3"):
        assert publisher.VERSION_RE.match(version)
        name = f"sosforge-v{version}.apk"
        assert "alias /srv/downloads/$1" in directives(resolve(f"/download/{name}")[1])
        assert "alias /srv/downloads/$1" in directives(resolve(f"/download/{name}.sha256")[1])
    assert resolve(f"/download/{publisher.LATEST_ALIAS}")[0].startswith("~")


def test_the_web_container_mounts_the_downloads_read_only():
    web = COMPOSE[COMPOSE.index("\n  web:") :]
    assert "- ./downloads:/srv/downloads:ro" in web
    api = COMPOSE[COMPOSE.index("\n  api:") : COMPOSE.index("\n  web:")]
    assert "downloads" not in api, "the API has no business reading the APKs"
