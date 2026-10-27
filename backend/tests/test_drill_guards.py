"""tools/drill.py is a fire drill, not data: these are the guards that keep
it from ever reaching anything real.

It must listen on loopback only (an emulator reaches it through `adb
reverse`, never a real device), journal into its own fresh temp dir only,
and publish events nobody can take for real ones: a source name no real
source uses, an id of its own, [DRILL] in the title and the place, no link
to an agency page.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def drill():
    spec = importlib.util.spec_from_file_location("sosforge_drill", ROOT / "tools" / "drill.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # registered first: its dataclasses look their module up by name
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


class TestLoopbackOnly:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
    def test_loopback_is_accepted(self, drill, host):
        assert drill.require_loopback(host) == host

    @pytest.mark.parametrize(
        "host",
        ["0.0.0.0", "::", "192.168.1.20", "10.0.2.2", "185.246.86.143", "sosforge.soclose.co", ""],
    )
    def test_anything_else_is_refused(self, drill, host):
        with pytest.raises(SystemExit, match="loopback only"):
            drill.require_loopback(host)


class TestOwnTempDirOnly:
    def test_its_own_fresh_temp_dir_is_accepted(self, drill, tmp_path):
        data = tmp_path / "sosforge-drill-abc123"
        data.mkdir()
        drill.require_drill_data_dir(data, data, data, temp_root=tmp_path)

    def test_the_products_data_dir_is_refused(self, drill, tmp_path):
        product = ROOT / "backend" / "data"
        with pytest.raises(SystemExit, match="refusing to run"):
            drill.require_drill_data_dir(product, product, product, temp_root=tmp_path)

    def test_a_deployed_volume_is_refused(self, drill, tmp_path):
        volume = Path("/root/SAAS/SuiteForge/SOSForge/backend/data")
        with pytest.raises(SystemExit, match="refusing to run"):
            drill.require_drill_data_dir(volume, volume, volume, temp_root=tmp_path)

    def test_a_temp_dir_it_did_not_create_is_refused(self, drill, tmp_path):
        other = tmp_path / "someone-elses"
        other.mkdir()
        with pytest.raises(SystemExit, match="refusing to run"):
            drill.require_drill_data_dir(other, other, other, temp_root=tmp_path)

    def test_a_store_opened_elsewhere_is_refused(self, drill, tmp_path):
        # the app imported before SOS_DATA_DIR was set: the settings may agree,
        # the store already journals into the product's directory
        data = tmp_path / "sosforge-drill-abc123"
        data.mkdir()
        product = ROOT / "backend" / "data"
        with pytest.raises(SystemExit, match="refusing to run"):
            drill.require_drill_data_dir(data, data, product, temp_root=tmp_path)
        with pytest.raises(SystemExit, match="refusing to run"):
            drill.require_drill_data_dir(data, product, data, temp_root=tmp_path)
        with pytest.raises(SystemExit, match="refusing to run"):
            drill.require_drill_data_dir(data, data, None, temp_root=tmp_path)

    def test_an_app_imported_before_isolation_is_refused(self, drill):
        import app.main  # noqa: F401 -- what any other test in this run has done

        with pytest.raises(SystemExit, match="imported before isolation"):
            drill.isolate()


class TestNothingLooksReal:
    def test_no_real_source_is_called_drill(self, drill):
        from app.main import build_sources

        names = {source.name for source in build_sources()}
        assert drill.DRILL_SOURCE not in names
        assert len(names) > 20  # the real list, not an empty one

    def test_every_scenario_event_is_marked(self, drill):
        player = drill.Player(main=None, timeline=None, run="abc123", speed=1.0)
        now = datetime.now(UTC)
        seen = 0
        for path in sorted((ROOT / "tools" / "drills").glob("0*.json")):
            scenario = json.loads(path.read_text(encoding="utf-8"))
            for step in scenario["steps"]:
                if "source" not in step:
                    continue
                payload = drill.resolve(step["payload"], now, "abc123")
                events, _ = player.normalize(step, payload)
                for event in events:
                    # the scenario labels it itself...
                    assert drill.DRILL_MARK in event.place, (path.name, event.place)
                    assert drill.DRILL_MARK in event.title, (path.name, event.title)
                    # ...and the drill makes it impossible to mistake
                    drill.mark_drill(event)
                    assert event.source == drill.DRILL_SOURCE
                    assert event.id.startswith("drill:")
                    assert event.url is None
                    seen += 1
        assert seen >= 10

    def test_an_unlabelled_event_gets_the_mark_anyway(self, drill):
        from app.models.event import Event

        event = Event(
            id="usgs:x1",
            source="usgs",
            source_id="x1",
            time=datetime.now(UTC),
            place="Somewhere",
            title="M 5.0 - Somewhere",
            url="https://earthquake.usgs.gov/x1",
        )
        drill.mark_drill(event)
        assert (event.source, event.id, event.source_id) == ("drill", "drill:usgs:x1", "usgs:x1")
        assert event.title.startswith("[DRILL]") and event.place.startswith("[DRILL]")
        assert event.url is None


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_serving(*extra: str):
    """`drill.py serve` in a real process, up to its banner."""
    import re
    import subprocess
    import time

    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "tools" / "drill.py"), "serve", "--port", str(port), *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.time() + 30
    data = None
    assert proc.stdout is not None
    while time.time() < deadline:
        line = proc.stdout.readline()
        match = re.search(r"data (\S+sosforge-drill-\S+?),", line)
        if match:
            data = Path(match.group(1))
            break
    assert data is not None and data.is_dir(), "the drill never printed its banner"
    return proc, port, data


@pytest.mark.parametrize("sig", ["SIGTERM", "SIGINT"])
def test_a_signal_while_waiting_for_clients_stops_and_cleans_up(sig):
    """The no-op handlers this replaced left `serve --wait-clients` waiting
    forever on a server that had already stopped, swallowing every later
    signal, with its temp dir never removed."""
    import signal
    import socket

    proc, port, data = _start_serving("--wait-clients", "1")
    try:
        proc.send_signal(getattr(signal, sig))
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert not data.exists(), "the temp data dir was left behind"
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass


def test_a_signal_while_serving_after_the_drills_stops_and_cleans_up():
    import signal

    proc, _, data = _start_serving("--scenario", "07-far-extreme", "--gap", "0")
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if "still serving" in line:
                break
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert not data.exists()
