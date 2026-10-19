"""HTTP surface: request hygiene and input validation. Offline: nothing here reaches the network or a model."""
import pytest
from fastapi.testclient import TestClient

import main

H = {"Host": "127.0.0.1:8750"}


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, base_url="http://127.0.0.1:8750")


def test_wrong_host_rejected(client):
    assert client.get("/api/status", headers={"Host": "evil.com"}).status_code == 403          # DNS rebinding


def test_cross_origin_post_rejected(client):
    r = client.post("/api/mode", json={"mode": "research"}, headers={"Origin": "http://evil.com"})
    assert r.status_code == 403


def test_non_json_post_rejected(client):
    assert client.post("/api/mode", content="mode=research", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415


def test_only_research_mode_enabled(client):
    assert client.post("/api/mode", json={"mode": "research"}).status_code == 200
    assert client.post("/api/mode", json={"mode": "paper"}).status_code == 403
    assert client.post("/api/mode", json={"mode": "live"}).status_code == 403
    assert client.post("/api/mode", json={"mode": "bogus"}).status_code == 400


def test_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/api/series/..%2Fetc", "/api/history/..%2Fx", "/api/quotes?symbols=AAPL,../x",
                                  "/api/reports/not-an-id", "/api/series/CPIAUCSL?units=drop", "/api/fred/search?q=a"])
def test_bad_inputs_rejected_before_any_network_call(client, path):
    assert client.get(path).status_code in (400, 404, 422)


def test_journal_log_validates_body(client):
    bad = {"symbol": "../../x", "direction": "long", "thesis": "t", "entry_price": 10, "horizon_days": 5, "probability": 0.5}
    assert client.post("/api/journal/log", json=bad).status_code == 422
    assert client.post("/api/journal/log", json={**bad, "symbol": "AAPL", "probability": 7}).status_code == 422


def test_analyze_rejects_short_tasks(client):
    assert client.post("/api/analyze", json={"task": "hi"}).status_code == 422


def test_unknown_job_404(client):
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_watchlist_close_series_handles_single_and_multi_symbol_frames():
    """Regression: a one-symbol watchlist showed 'no data' because yfinance returns a differently shaped frame."""
    import pandas as pd
    idx = pd.date_range("2026-01-01", periods=3)
    multi = pd.DataFrame({("AAA", "Close"): [1.0, 2.0, 3.0], ("BBB", "Close"): [4.0, 5.0, 6.0]}, index=idx)
    multi.columns = pd.MultiIndex.from_tuples(multi.columns)
    flat = pd.DataFrame({"Close": [1.0, 2.0, 3.0], "Open": [1, 1, 1]}, index=idx)
    one = pd.DataFrame({("AAA", "Close"): [7.0, 8.0, 9.0]}, index=idx)
    one.columns = pd.MultiIndex.from_tuples(one.columns)
    assert list(main._close_series(multi, "BBB")) == [4.0, 5.0, 6.0]
    assert list(main._close_series(flat, "AAA")) == [1.0, 2.0, 3.0]
    assert list(main._close_series(one, "AAA")) == [7.0, 8.0, 9.0]
