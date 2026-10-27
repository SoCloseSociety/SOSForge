"""tools/publish_apk.py -- what the public download page and the app's update
check believe about a release.

The tool output fixtures are verbatim: `aapt2 dump badging` and `apksigner
verify --print-certs` run on the real sosforge-v0.1.1.apk, and apksigner on
the same APK re-signed with the local Android debug key (the "someone else's
key" case, produced for real, not typed). The last test runs the real tools
on the real APK when this machine has both.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import http.server
import importlib.util
import json
import re
import shlex
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
REAL_APK = Path("/Users/neo/Documents/VsCodeN30/_apk-drops/sosforge/sosforge-v0.1.1.apk")

spec = importlib.util.spec_from_file_location("publish_apk", ROOT / "tools" / "publish_apk.py")
assert spec and spec.loader
publish_apk = importlib.util.module_from_spec(spec)
# A dataclass resolves its module through sys.modules while it is created.
sys.modules["publish_apk"] = publish_apk
spec.loader.exec_module(publish_apk)

BADGING = (FIXTURES / "apk_sosforge-v0.1.1_aapt2-badging.txt").read_text()
RELEASE_SIGNER = (FIXTURES / "apk_sosforge-v0.1.1_apksigner-print-certs.txt").read_text()
DEBUG_SIGNER = (FIXTURES / "apk_debug-resigned_apksigner-print-certs.txt").read_text()
TODAY = dt.date(2026, 10, 27)


def contract() -> dict:
    """The latest.json contract as tasks/todo.md writes it (lane A reads it)."""
    text = (ROOT / "tasks" / "todo.md").read_text()
    section = text[text.index("## Contract: the APK download") :]
    block = re.search(r"```json\n(.*?)\n```", section, re.S)
    assert block, "the contract block moved"
    return json.loads(block.group(1))


def runner(signer: str = RELEASE_SIGNER, verify_code: int = 0, badging: str = BADGING):
    def run(cmd):
        tool = Path(cmd[0]).name
        if tool == "aapt2":
            return subprocess.CompletedProcess(cmd, 0, badging, "")
        if tool == "apksigner":
            return subprocess.CompletedProcess(cmd, verify_code, signer, "DOES NOT VERIFY")
        raise AssertionError(cmd)

    return run


def apk(tmp_path: Path, content: bytes = b"PK\x03\x04 release bytes") -> Path:
    path = tmp_path / "in" / "app-release.apk"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


def publish(tmp_path: Path, src: Path, **kw):
    kw.setdefault("run", runner())
    kw.setdefault("today", TODAY)
    return publish_apk.publish(src, tmp_path / "out", tools=Path("/sdk"), **kw)


def leftovers(out: Path) -> list[str]:
    return sorted(p.name for p in out.iterdir() if p.name.startswith("."))


def test_badging_of_the_real_apk():
    info = publish_apk.parse_badging(BADGING)
    assert info == publish_apk.Badging("co.soclose.sosforge", "0.1.1", 6, 26)


def test_signers_of_the_real_apk_and_of_a_debug_resign():
    assert publish_apk.parse_signers(RELEASE_SIGNER) == [publish_apk.RELEASE_CERT_SHA256]
    assert publish_apk.parse_signers(DEBUG_SIGNER) == [
        "89f5e431c2806c5b3ee781711b6a1a2f5aac7ac4051f3e2e54d05140f2832ae8"
    ]


def test_latest_json_is_exactly_the_contract(tmp_path):
    src = apk(tmp_path)
    latest = publish(tmp_path, src, notes_en="en", notes_fr="fr")
    out = tmp_path / "out"
    on_disk = json.loads((out / "latest.json").read_text())

    expected = contract()
    assert list(on_disk) == list(expected), "keys or their order drifted from the contract"
    assert list(on_disk["notes"]) == list(expected["notes"])
    for key, value in expected.items():
        assert type(on_disk[key]) is type(value), key

    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    assert on_disk == latest
    assert on_disk["app"] == "co.soclose.sosforge"
    assert on_disk["version"] == "0.1.1"
    assert on_disk["version_code"] == 6
    assert on_disk["url"] == "/download/sosforge-v0.1.1.apk"
    assert on_disk["size"] == src.stat().st_size
    assert on_disk["sha256"] == sha
    assert on_disk["cert_sha256"] == publish_apk.RELEASE_CERT_SHA256
    assert on_disk["min_sdk"] == 26
    assert on_disk["released"] == "2026-10-27"

    published = out / "sosforge-v0.1.1.apk"
    assert published.read_bytes() == src.read_bytes()
    # `sha256sum -c` format: hash, two spaces, the file name next to it.
    assert (out / "sosforge-v0.1.1.apk.sha256").read_text() == f"{sha}  sosforge-v0.1.1.apk\n"
    assert (out / "sosforge-latest.apk").samefile(published)
    assert leftovers(out) == []


def test_a_foreign_signer_publishes_nothing(tmp_path):
    with pytest.raises(publish_apk.Refused, match="not by the release key"):
        publish(tmp_path, apk(tmp_path), run=runner(signer=DEBUG_SIGNER))
    assert list((tmp_path / "out").iterdir()) == []


def test_a_signature_that_does_not_verify_publishes_nothing(tmp_path):
    with pytest.raises(publish_apk.Refused, match="does not verify"):
        publish(tmp_path, apk(tmp_path), run=runner(verify_code=1))
    assert list((tmp_path / "out").iterdir()) == []


def test_another_package_is_refused(tmp_path):
    other = BADGING.replace("name='co.soclose.sosforge'", "name='com.example.other'", 1)
    with pytest.raises(publish_apk.Refused, match="package"):
        publish(tmp_path, apk(tmp_path), run=runner(badging=other))


def test_a_published_version_is_never_overwritten_with_other_bytes(tmp_path):
    publish(tmp_path, apk(tmp_path, b"PK first build"))
    before = (tmp_path / "out" / "latest.json").read_text()
    with pytest.raises(publish_apk.Refused, match="bump the version"):
        publish(tmp_path, apk(tmp_path, b"PK second build, same versionCode"))
    out = tmp_path / "out"
    assert (out / "latest.json").read_text() == before
    assert (out / "sosforge-v0.1.1.apk").read_bytes() == b"PK first build"
    assert leftovers(out) == []


def test_a_lower_version_code_is_refused(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    newer = {**contract(), "version": "0.1.2", "version_code": 7, "sha256": "0" * 64}
    (out / "latest.json").write_text(json.dumps(newer))
    with pytest.raises(publish_apk.Refused, match="lower"):
        publish(tmp_path, apk(tmp_path))
    assert json.loads((out / "latest.json").read_text())["version_code"] == 7


def test_republishing_the_same_release_changes_nothing(tmp_path):
    src = apk(tmp_path)
    first = publish(tmp_path, src, notes_en="en", notes_fr="fr")
    again = publish(tmp_path, src, today=TODAY + dt.timedelta(days=3))
    assert again == first, "a re-run moved the release date or dropped the notes"
    assert leftovers(tmp_path / "out") == []


def test_the_download_page_explains_exactly_what_the_real_apk_asks_for():
    """The page gives a reason per permission. Read from the real 0.1.1
    badging, not from the manifest source: the build merges in permissions
    the source never names (WAKE_LOCK from WorkManager, the AndroidX
    receiver one)."""
    asked = publish_apk.parse_permissions(BADGING)
    assert "android.permission.ACCESS_COARSE_LOCATION" in asked
    assert "android.permission.ACCESS_FINE_LOCATION" not in asked
    assert publish_apk.page_permissions() == asked


def test_no_page_carries_a_typed_software_version():
    """softwareVersion lives only in what download.js builds from latest.json."""
    assert publish_apk.stale_json_ld("any") == []
    for page in ("frontend/index.html", "frontend/public/download/index.html"):
        assert "softwareVersion" not in (ROOT / page).read_text(), page


class FakeSite:
    """A web server answering like the deployed nginx does: files from a
    directory under /download/, 404 otherwise; or one fixed answer for
    everything (the app shell, before the download routes existed)."""

    def __init__(self, root: Path, html_for_everything: bool = False):
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 -- the stdlib's name
                if html_for_everything:
                    return site.answer(self, 200, "text/html", b"<!doctype html><title>SOSForge")
                name = self.path.removeprefix("/download/")
                path = root / name
                if "/" in name or not path.is_file():
                    return site.answer(self, 404, "text/html", b"Not Found")
                ctype = "application/json" if name.endswith(".json") else "text/plain"
                return site.answer(self, 200, ctype, path.read_bytes())

            def do_HEAD(self):  # noqa: N802 -- the stdlib's name
                name = self.path.removeprefix("/download/")
                ok = not html_for_everything and "/" not in name and (root / name).is_file()
                self.send_response(200 if ok else 404)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @staticmethod
    def answer(handler, status: int, ctype: str, body: bytes) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served(tmp_path):
    """What the server serves, and the site serving it."""
    root = tmp_path / "server"
    root.mkdir()
    site = FakeSite(root)
    yield root, site.url
    site.close()


class TestTheServerIsTheReference:
    """downloads/ is gitignored: another clone starts empty and saw nothing to
    refuse. The checks now also read what the server serves."""

    def test_an_empty_clone_cannot_publish_below_the_served_version(self, tmp_path, served):
        root, url = served
        (root / "latest.json").write_text(
            json.dumps({**contract(), "version": "0.1.2", "version_code": 7, "sha256": "0" * 64})
        )
        with pytest.raises(publish_apk.Refused, match="lower than the one published on"):
            publish(tmp_path, apk(tmp_path), server=url)
        assert list((tmp_path / "out").iterdir()) == []

    def test_an_empty_clone_cannot_replace_the_bytes_of_a_served_version(self, tmp_path, served):
        root, url = served
        (root / "sosforge-v0.1.1.apk.sha256").write_text(f"{'a' * 64}  sosforge-v0.1.1.apk\n")
        with pytest.raises(publish_apk.Refused, match="already served with sha256"):
            publish(tmp_path, apk(tmp_path), server=url)
        assert list((tmp_path / "out").iterdir()) == []

    def test_a_served_apk_without_its_checksum_is_a_refusal(self, tmp_path, served):
        root, url = served
        (root / "sosforge-v0.1.1.apk").write_bytes(b"PK other bytes, half uploaded")
        with pytest.raises(publish_apk.Refused, match="without its .sha256"):
            publish(tmp_path, apk(tmp_path), server=url)

    def test_republishing_what_is_served_keeps_its_date(self, tmp_path, served):
        root, url = served
        src = apk(tmp_path)
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        served_release = {**contract(), "version": "0.1.1", "version_code": 6, "sha256": sha}
        served_release["released"] = "2026-10-20"
        (root / "latest.json").write_text(json.dumps(served_release))
        (root / "sosforge-v0.1.1.apk.sha256").write_text(f"{sha}  sosforge-v0.1.1.apk\n")
        assert publish(tmp_path, src, server=url)["released"] == "2026-10-20"

    def test_a_server_with_nothing_published_accepts_the_first_release(self, tmp_path, served):
        _, url = served
        assert publish(tmp_path, apk(tmp_path), server=url)["version_code"] == 6

    def test_a_server_that_does_not_serve_the_contract_is_a_refusal(self, tmp_path):
        site = FakeSite(tmp_path, html_for_everything=True)
        try:
            with pytest.raises(publish_apk.Refused, match="not the release JSON"):
                publish(tmp_path, apk(tmp_path), server=site.url)
        finally:
            site.close()

    def test_an_unreachable_server_is_a_refusal(self, tmp_path):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with pytest.raises(publish_apk.Refused, match="cannot reach"):
            publish(tmp_path, apk(tmp_path), server=f"http://127.0.0.1:{port}")


def rsync_sends(command: str, files: list[str]) -> list[str]:
    """Which of `files` (names in downloads/) an rsync command line sends,
    following rsync's first-matching-rule order for --include/--exclude."""
    words = shlex.split(command)
    rules = [(w, words[i + 1]) for i, w in enumerate(words) if w in ("--include", "--exclude")]
    sources = [w for w in words[1:-1] if not w.startswith("-") and w not in [r[1] for r in rules]]
    sent = []
    for source in sources:
        if not source.endswith("/"):
            sent.append(Path(source).name)  # one named file
            continue
        for name in files:
            verdict = next((kind for kind, pat in rules if fnmatch.fnmatch(name, pat)), "--include")
            if verdict == "--include":
                sent.append(name)
    return sent


def test_the_documented_copy_sends_only_release_files_and_never_over_a_served_one():
    files = [
        ".gitignore",
        "README.md",
        "latest.json",
        "sosforge-latest.apk",
        "sosforge-v0.1.1.apk",
        "sosforge-v0.1.1.apk.sha256",
    ]
    steps = publish_apk.rsync_steps()
    readme = (ROOT / "downloads" / "README.md").read_text()
    for step in steps:
        assert step in readme, "README and tool disagree on how files reach the server"
    first, second = (rsync_sends(s, files) for s in steps)
    assert sorted(first) == ["sosforge-v0.1.1.apk", "sosforge-v0.1.1.apk.sha256"]
    assert "--ignore-existing" in steps[0]
    assert sorted(second) == ["latest.json", "sosforge-latest.apk"]
    assert "README.md" not in first + second and ".gitignore" not in first + second


@pytest.mark.skipif(not REAL_APK.exists(), reason="the real APK is only on the Mac")
def test_the_real_apk_through_the_real_tools(tmp_path):
    try:
        tools = publish_apk.find_build_tools()
    except publish_apk.Refused:
        pytest.skip("no Android build-tools here")
    latest = publish_apk.publish(REAL_APK, tmp_path, tools=tools, today=TODAY)
    assert latest["sha256"] == "971ebc3200517b0f541413e0338bf5a1ddd39721937b87a4bb94f1ff34a8b22f"
    assert latest["size"] == 38946728
    assert (latest["version"], latest["version_code"], latest["min_sdk"]) == ("0.1.1", 6, 26)
    assert latest["cert_sha256"] == publish_apk.RELEASE_CERT_SHA256
