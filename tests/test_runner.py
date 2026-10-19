"""The runner must enforce its timeout and Cancel even when `claude` is completely silent."""
import threading
import time

import claude_runner as cr
import pytest


@pytest.fixture()
def silent_claude(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\ncat >/dev/null &\nsleep 30\n")   # reads nothing, prints nothing, never finishes
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setattr(cr.mcp_config, "keychain_get", lambda n: None)


def test_timeout_fires_without_any_output(silent_claude):
    t0 = time.time()
    r = cr.run_claude("hi", "sys", cr.SONNET, timeout_s=2)
    assert r.error == "timed out after 2s" and not r.ok
    assert time.time() - t0 < 10


def test_cancel_kills_a_silent_run(silent_claude):
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    t0 = time.time()
    r = cr.run_claude("hi", "sys", cr.SONNET, timeout_s=60, cancel=cancel)
    assert r.error == "cancelled" and time.time() - t0 < 10


def test_mcp_config_temp_file_is_removed_and_private(silent_claude, monkeypatch):
    import glob
    import os
    import tempfile
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "econagent-mcp-*.json")))
    cr.run_claude("hi", "sys", cr.SONNET, timeout_s=1)
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "econagent-mcp-*.json"))) == before
