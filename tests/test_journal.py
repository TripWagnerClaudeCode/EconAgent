import json

import pytest
from conftest import load_module


@pytest.fixture()
def j(tmp_path, monkeypatch):
    monkeypatch.setenv("ECONAGENT_JOURNAL_DB", str(tmp_path / "j.db"))
    return load_module("journal_under_test", "mcp/journal/server.py")


def _log(j, sym="AAA", direction="long", prob=0.7, kronos=False, entry=100.0):
    return json.loads(j.journal_log_idea(sym, direction, "t", entry, 30, prob, kronos_used=kronos))


def test_validation(j):
    assert "error" in json.loads(j.journal_log_idea("X", "buy", "t", 1, 1, 0.5))
    assert "error" in json.loads(j.journal_log_idea("X", "long", "t", 1, 1, 1.5))
    assert "error" in json.loads(j.journal_log_idea("X", "long", "t", 1, 0, 0.5))


def test_long_and_short_returns(j):
    a, b = _log(j)["id"], _log(j, direction="short")["id"]
    assert json.loads(j.journal_close_idea(a, 115, "hit"))["realized_return_pct"] == 15.0
    assert json.loads(j.journal_close_idea(b, 85, "hit"))["realized_return_pct"] == 15.0   # short profits when price falls


def test_cannot_close_twice_or_unknown(j):
    i = _log(j)["id"]
    j.journal_close_idea(i, 100, "partial")
    assert "already closed" in json.loads(j.journal_close_idea(i, 1, "hit"))["error"]
    assert "no idea" in json.loads(j.journal_close_idea(999, 1, "hit"))["error"]


def test_brier_and_calibration(j):
    ids = [_log(j, prob=p, kronos=k)["id"] for p, k in ((0.8, True), (0.7, False), (0.6, True))]
    for i, (px, oc) in zip(ids, ((115, "hit"), (90, "miss"), (110, "hit")), strict=True):
        j.journal_close_idea(i, px, oc)
    c = json.loads(j.journal_calibration())
    assert c["closed"] == 3 and c["brier"] == pytest.approx(((0.2) ** 2 + 0.7 ** 2 + 0.4 ** 2) / 3, abs=1e-3)
    assert c["kronos_assisted"]["n"] == 2 and c["not_kronos"]["n"] == 1


def test_empty_journal(j):
    assert json.loads(j.journal_calibration())["closed"] == 0
