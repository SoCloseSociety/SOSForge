"""tools/drills/web_alarm.py tears down what it started, and only that.

PR #5 review, finding 2: the drill server was launched before the
try/finally that stops it, so a Vite launch that failed (no `npx` on PATH)
left the drill waiting for a client forever and its timeline dir behind. And
the teardown stopped servers BY PORT, which kills whoever holds the port by
then (lesson 30).
"""

from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def web_alarm():
    path = ROOT / "tools" / "drills" / "web_alarm.py"
    spec = importlib.util.spec_from_file_location("sosforge_web_alarm", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # no Playwright needed until a browser runs
    yield module
    sys.modules.pop(spec.name, None)


def test_a_failed_vite_launch_still_stops_the_drill_and_removes_the_timeline(
    web_alarm, monkeypatch, tmp_path
):
    started: list[object] = []
    signalled: list[tuple[int, int]] = []
    made: list[str] = []
    real_mkdtemp = web_alarm.tempfile.mkdtemp

    class FakeProc:
        def __init__(self, argv, **kwargs):
            if argv[0] == "npx":
                raise FileNotFoundError("npx")
            assert kwargs.get("start_new_session") is True
            self.pid = 424242
            started.append(self)

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    def mkdtemp(**kwargs):
        made.append(real_mkdtemp(dir=tmp_path, **kwargs))
        return made[-1]

    monkeypatch.setattr(web_alarm.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(web_alarm.os, "killpg", lambda pid, sig: signalled.append((pid, sig)))
    monkeypatch.setattr(web_alarm.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(web_alarm, "free", lambda port: True)

    with pytest.raises(FileNotFoundError):
        web_alarm.main(["--drill-port", "1", "--vite-port", "2"])

    assert len(started) == 1, "the drill was started"
    assert signalled == [(424242, signal.SIGTERM)], "and stopped, by its own PID"
    assert made and not Path(made[0]).exists(), "and the timeline dir is gone"


def test_teardown_reaches_the_children_of_a_leader_that_already_exited(web_alarm):
    """`npx` can exit while the node server it started keeps listening: the
    teardown signals the whole group, by the PID it recorded."""
    owned = web_alarm.Owned()
    leader = owned.start(
        ["sh", "-c", "sleep 60 & echo $!; exit 0"], stdout=subprocess.PIPE, text=True
    )
    assert leader.stdout is not None
    child = int(leader.stdout.readline())
    leader.wait(timeout=5)
    os.kill(child, 0)  # the child outlived its leader
    owned.stop(grace=5)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the orphaned child is still running")


def test_it_never_stops_anything_by_port(web_alarm):
    source = (ROOT / "tools" / "drills" / "web_alarm.py").read_text()
    assert "stop_port" not in source
    assert "xargs kill" not in source
