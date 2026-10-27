#!/usr/bin/env python3
"""Publishes a signed SOSForge APK for download from the public site.

    python3 tools/publish_apk.py path/to/app.apk [--out downloads] \
        [--notes-en "..."] [--notes-fr "..."]

    (or: make publish-apk APK=path/to/app.apk NOTES_EN="..." NOTES_FR="...")

What it writes into the output directory (default `downloads/`, which the web
container serves read-only under /download/, see deploy/nginx.conf):

    sosforge-v<version>.apk          the APK, byte for byte
    sosforge-v<version>.apk.sha256   `sha256sum -c` format
    sosforge-latest.apk              a hard link to the newest one, for the
                                     page's no-JavaScript link
    latest.json                      the contract the Android app reads for its
                                     update check (tasks/todo.md, "Round 2")

Every field of latest.json is READ BACK from the APK, never typed: package,
version and minSdk from `aapt2 dump badging`, the signing certificate from
`apksigner verify --print-certs`, size and sha256 from the very bytes that are
published. A version typed by hand is a version that can disagree with the
file, and the app compares it to its own to decide whether to offer an update.

Refusals, each one a way to ship something that lies:
  - a signature that does not verify, or a signer other than the release key
    (an update signed by another key cannot install over the app anyway: the
    user would download 39 MB to be told "App not installed");
  - another package name;
  - the same version with different bytes: versioned files are served
    `immutable` for a year, so a browser or a CDN that already holds the old
    bytes would keep them, and the published sha256 would match neither;
  - a lower versionCode than the one published (unless --allow-downgrade).
"Published" means both this output directory AND what the server serves
(--server, default https://sosforge.soclose.co: its latest.json and the
version's .sha256). downloads/ is gitignored, so another clone or worktree
starts empty and saw nothing to refuse; unreachable or not serving the
contract is a refusal too, unless --no-server-check says so on purpose.

The order of the writes matters: latest.json is renamed into place LAST, so
the app and the page never read a pointer to a file that is not there yet.
Old versions are never deleted: their URLs are immutable, and a page or a
search result may still link to one. The tool ends by printing the two
rsync steps that put the files on the server in that same order
(rsync_steps); downloads/README.md shows them too.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "co.soclose.sosforge"
# The release key: CN=SOSForge, O=SoClose Pte. Ltd., C=SG (kept in
# ~/.soclose/android, never in the repository). Android refuses an update
# signed by any other key, so a mismatch here is never "probably fine".
RELEASE_CERT_SHA256 = "610d345a896dfc71bd0ddc4122668c9ffcce0e16bc495d6ed4f573812a98bdf8"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "downloads"
URL_PREFIX = "/download/"
LATEST_ALIAS = "sosforge-latest.apk"

# What is actually served. downloads/ is gitignored, so every clone and every
# worktree has its own, often empty: checking only the local copy let a
# second machine publish a lower versionCode, or other bytes under a version
# browsers hold for a year (PR #5 review). The server's word is checked too.
DEFAULT_SERVER = "https://sosforge.soclose.co"
REMOTE = "helper-vps:/root/SAAS/SuiteForge/SOSForge/downloads/"


def rsync_steps(out: str = "downloads/", remote: str = REMOTE) -> tuple[str, str]:
    """How the files reach the server, in this order (downloads/README.md
    shows the same two lines; a test keeps them equal):
    1. the versioned files only, and never over a file the server already
       has (--ignore-existing): a served version is immutable;
    2. then the two pointers, which do change at every release.
    README.md and .gitignore stay out: they are tracked in the server's git
    tree, and an rsynced copy would make its next `git pull` refuse."""
    out = out.rstrip("/") + "/"
    return (
        f"rsync -av --ignore-existing --include 'sosforge-v*' --exclude '*' {out} {remote}",
        f"rsync -av {out}latest.json {out}{LATEST_ALIAS} {remote}",
    )


# Used in a file name and in nginx's location regex: digits first, then a
# conservative alphabet. Anything else is refused, not escaped.
VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z._-]{0,31}$")

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class Refused(Exception):
    """The APK must not be published, and why."""


@dataclass(frozen=True)
class Badging:
    package: str
    version: str
    version_code: int
    min_sdk: int


def parse_badging(text: str) -> Badging:
    """Reads `aapt2 dump badging`. The first line carries the package; the
    minimum SDK is `minSdkVersion:'26'` (aapt2) or `sdkVersion:'26'` (aapt)."""
    package = re.search(r"^package: name='([^']+)'", text, re.MULTILINE)
    code = re.search(r"^package:.*\bversionCode='(\d+)'", text, re.MULTILINE)
    name = re.search(r"^package:.*\bversionName='([^']*)'", text, re.MULTILINE)
    min_sdk = re.search(r"^(?:minSdkVersion|sdkVersion):'(\d+)'", text, re.MULTILINE)
    if not (package and code and name and min_sdk):
        raise Refused("aapt2 did not report package, versionCode, versionName and minSdk")
    return Badging(package.group(1), name.group(1), int(code.group(1)), int(min_sdk.group(1)))


def parse_signers(text: str) -> list[str]:
    """The certificate SHA-256 of every signer `apksigner verify
    --print-certs` lists, lower case."""
    return [
        m.lower()
        for m in re.findall(
            r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$", text, re.MULTILINE
        )
    ]


def parse_permissions(text: str) -> set[str]:
    """Every `uses-permission` of `aapt2 dump badging`."""
    return set(re.findall(r"^uses-permission: name='([^']+)'", text, re.MULTILINE))


def page_permissions() -> set[str]:
    """The permissions the download page explains (its `data-permission`
    attributes): the page promises a reason for each one the app asks for."""
    page = ROOT / "frontend" / "public" / "download" / "index.html"
    found: set[str] = set()
    for attr in re.findall(r'data-permission="([^"]+)"', page.read_text()):
        found.update(attr.split())
    return found


def find_build_tools() -> Path:
    """The newest Android build-tools directory holding both aapt2 and
    apksigner: $ANDROID_BUILD_TOOLS, else $ANDROID_HOME / $ANDROID_SDK_ROOT /
    ~/Library/Android/sdk + build-tools/<newest>."""
    explicit = os.environ.get("ANDROID_BUILD_TOOLS")
    if explicit:
        return Path(explicit)
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env):
            sdk = Path(os.environ[env])
            break
    else:
        sdk = Path.home() / "Library" / "Android" / "sdk"

    def numeric(p: Path) -> tuple[int, ...]:
        return tuple(int(x) if x.isdigit() else -1 for x in re.split(r"[.-]", p.name))

    candidates = sorted((sdk / "build-tools").glob("*"), key=numeric, reverse=True)
    for d in candidates:
        if (d / "aapt2").exists() and (d / "apksigner").exists():
            return d
    raise Refused(f"no build-tools with aapt2 + apksigner under {sdk} (set ANDROID_BUILD_TOOLS)")


def run_tool(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def read_apk(apk: Path, tools: Path, run: Runner = run_tool) -> tuple[Badging, str]:
    """Package/version/minSdk and the single signer's certificate SHA-256."""
    badging = run([str(tools / "aapt2"), "dump", "badging", str(apk)])
    if badging.returncode != 0:
        raise Refused(f"aapt2 could not read the APK: {badging.stderr.strip()[:300]}")
    info = parse_badging(badging.stdout)

    verify = run([str(tools / "apksigner"), "verify", "--print-certs", str(apk)])
    if verify.returncode != 0:
        raise Refused(
            f"the signature does not verify: {(verify.stderr or verify.stdout).strip()[:300]}"
        )
    signers = parse_signers(verify.stdout)
    if len(signers) != 1:
        raise Refused(f"expected exactly one signer, apksigner reports {len(signers)}")
    return info, signers[0]


def _get(url: str, timeout: float = 20.0, method: str = "GET") -> tuple[int, str, bytes]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "sosforge-publish-apk", "Cache-Control": "no-cache"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, "", b""
    except (urllib.error.URLError, OSError) as error:
        raise Refused(
            f"cannot reach {url} ({error}): the served release must be checked before "
            "publishing over it (--no-server-check skips it, knowingly)"
        ) from error


def server_release(server: str) -> dict | None:
    """The served latest.json, or None when the server has published none
    (404). Anything else that is not the contract is a refusal: an HTML page
    here means the web image does not serve /download/ yet."""
    url = f"{server}/download/latest.json"
    status, ctype, body = _get(url)
    if status == 404:
        return None
    if status != 200 or "json" not in ctype:
        raise Refused(f"{url} answered {status} {ctype or '?'}, not the release JSON")
    try:
        data = json.loads(body)
    except ValueError as error:
        raise Refused(f"{url} is not JSON") from error
    if not isinstance(data, dict) or not {"version", "version_code", "sha256"} <= data.keys():
        raise Refused(f"{url} is not the latest.json contract")
    return data


def server_sha256(server: str, name: str) -> str | None:
    """The checksum the server publishes for a versioned file, or None."""
    url = f"{server}{URL_PREFIX}{name}.sha256"
    status, _, body = _get(url)
    if status == 404:
        return None
    match = re.match(rb"\s*([0-9a-f]{64})\b", body) if status == 200 else None
    if not match:
        raise Refused(f"{url} answered {status} without a checksum")
    return match.group(1).decode()


def refuse_against(
    published: dict | None, info: Badging, sha: str, allow_downgrade: bool, where: str
) -> None:
    if not published:
        return
    if published["version_code"] > info.version_code and not allow_downgrade:
        raise Refused(
            f"versionCode {info.version_code} is lower than the one published {where}: "
            f"{published['version_code']} ({published['version']}); --allow-downgrade to force"
        )
    if published["version_code"] == info.version_code and published["sha256"] != sha:
        raise Refused(
            f"versionCode {info.version_code} is already published {where} with other bytes "
            f"({published['sha256']}): bump the version"
        )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def link_latest(out: Path, target: Path) -> None:
    """`sosforge-latest.apk` -> the newest versioned file, swapped atomically.
    A hard link costs no space; a filesystem without them gets a copy.

    Already the same file: nothing to do, and nothing must be tried. rename()
    between two links of one inode "does nothing and returns success"
    (POSIX), so the temporary link was left behind on every re-run."""
    alias = out / LATEST_ALIAS
    if alias.exists() and os.path.samefile(alias, target):
        return
    tmp = out / f".{LATEST_ALIAS}.tmp"
    tmp.unlink(missing_ok=True)
    try:
        os.link(target, tmp)
    except OSError:
        shutil.copyfile(target, tmp)
        os.chmod(tmp, 0o644)
    os.replace(tmp, out / LATEST_ALIAS)


def publish(
    apk: Path,
    out: Path,
    notes_en: str = "",
    notes_fr: str = "",
    *,
    tools: Path | None = None,
    run: Runner = run_tool,
    today: dt.date | None = None,
    allow_downgrade: bool = False,
    server: str | None = None,
) -> dict:
    """Copies, verifies the COPY (the bytes that will be served), checks it
    against what is published (here, and on `server` when given), then moves
    everything into place, latest.json last. Returns what latest.json says."""
    if not apk.is_file():
        raise Refused(f"no such file: {apk}")
    out.mkdir(parents=True, exist_ok=True)
    tools = tools or find_build_tools()

    staged = out / f".incoming-{os.getpid()}.apk"
    shutil.copyfile(apk, staged)
    try:
        info, cert = read_apk(staged, tools, run)
        if info.package != PACKAGE:
            raise Refused(f"package is {info.package}, not {PACKAGE}")
        if cert != RELEASE_CERT_SHA256:
            raise Refused(f"signed by {cert}, not by the release key {RELEASE_CERT_SHA256}")
        if not VERSION_RE.match(info.version):
            raise Refused(f"versionName {info.version!r} is not usable in a file name")
        sha = sha256_of(staged)
        size = staged.stat().st_size

        name = f"sosforge-v{info.version}.apk"
        latest_path = out / "latest.json"
        previous = json.loads(latest_path.read_text()) if latest_path.exists() else None
        refuse_against(previous, info, sha, allow_downgrade, f"in {out}")
        remote = None
        if server:
            remote = server_release(server)
            refuse_against(remote, info, sha, allow_downgrade, f"on {server}")
            served = server_sha256(server, name)
            if served and served != sha:
                raise Refused(
                    f"{server}{URL_PREFIX}{name} is already served with sha256 {served}: a "
                    "versioned file is immutable (cached for a year), bump the version"
                )
            # The APK there without its checksum is a half-done upload: the
            # copy step would add a .sha256 for OTHER bytes (rsync never
            # overwrites the served APK). Measured with rsync 3.5.1.
            if served is None and _get(f"{server}{URL_PREFIX}{name}", method="HEAD")[0] == 200:
                raise Refused(
                    f"{server}{URL_PREFIX}{name} is served without its .sha256: check the "
                    "server by hand before publishing over it"
                )

        target = out / name
        if target.exists():
            existing = sha256_of(target)
            if existing != sha:
                raise Refused(
                    f"{name} already exists with sha256 {existing}: a versioned file is immutable "
                    "(served cached for a year), bump the version"
                )
            staged.unlink()
        else:
            os.chmod(staged, 0o644)
            os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)

    write_atomic(out / f"{name}.sha256", f"{sha}  {name}\n")
    link_latest(out, target)

    # Republishing the same release keeps its date: it was released then --
    # as this directory or the server remembers it.
    same = next((p for p in (previous, remote) if p and p.get("sha256") == sha), None)
    released = same["released"] if same else (today or dt.datetime.now(dt.UTC).date()).isoformat()
    if same and not notes_en and not notes_fr:
        notes_en, notes_fr = same["notes"]["en"], same["notes"]["fr"]
    latest = {
        "app": PACKAGE,
        "version": info.version,
        "version_code": info.version_code,
        "url": URL_PREFIX + name,
        "size": size,
        "sha256": sha,
        "cert_sha256": cert,
        "min_sdk": info.min_sdk,
        "released": released,
        "notes": {"en": notes_en, "fr": notes_fr},
    }
    write_atomic(latest_path, json.dumps(latest, ensure_ascii=False, indent=1) + "\n")
    return latest


def stale_json_ld(version: str) -> list[str]:
    """Pages whose structured data names another softwareVersion: they ship in
    the web image, so they need an edit and a rebuild, not a re-publish."""
    stale = []
    for page in (
        ROOT / "frontend" / "index.html",
        ROOT / "frontend" / "public" / "download" / "index.html",
    ):
        if not page.exists():
            continue
        for found in re.findall(r'"softwareVersion":\s*"([^"]+)"', page.read_text()):
            if found != version:
                stale.append(f"{page.relative_to(ROOT)} says {found}")
    return stale


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("apk", type=Path)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--notes-en", default="")
    ap.add_argument("--notes-fr", default="")
    ap.add_argument("--allow-downgrade", action="store_true")
    ap.add_argument("--server", default=DEFAULT_SERVER, help="the site whose release is checked")
    ap.add_argument(
        "--no-server-check",
        action="store_true",
        help="do not compare with what the server serves (a first publish to a new host)",
    )
    args = ap.parse_args(argv)
    server = None if args.no_server_check else args.server.rstrip("/")
    try:
        latest = publish(
            args.apk,
            args.out,
            args.notes_en,
            args.notes_fr,
            allow_downgrade=args.allow_downgrade,
            server=server,
        )
    except Refused as why:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 1
    print(json.dumps(latest, ensure_ascii=False, indent=1))
    print(f"published into {args.out.resolve()}")
    if server is None:
        print("note: NOT checked against the served release (--no-server-check)")
    print("then, in this order:")
    shown = os.path.relpath(args.out)
    if shown.startswith(".."):
        shown = str(args.out.resolve())
    for step in rsync_steps(shown + "/"):
        print(f"  {step}")
    if not (latest["notes"]["en"] and latest["notes"]["fr"]):
        print("note: release notes are empty (--notes-en / --notes-fr)")
    for line in stale_json_ld(latest["version"]):
        print(f"note: JSON-LD out of date, {line}: edit it and rebuild the web image")
    published = args.out / latest["url"].rsplit("/", 1)[1]
    badging = run_tool([str(find_build_tools() / "aapt2"), "dump", "badging", str(published)])
    asked, explained = parse_permissions(badging.stdout), page_permissions()
    for perm in sorted(asked - explained):
        print(f"note: the APK asks for {perm} and the download page does not say why")
    for perm in sorted(explained - asked):
        print(f"note: the download page explains {perm}, which this APK no longer asks for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
