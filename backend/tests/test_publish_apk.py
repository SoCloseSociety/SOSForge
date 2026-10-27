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
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
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
