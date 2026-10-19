import json

import httpx
import pytest
from conftest import load_module

eg = load_module("econ_gov_under_test", "mcp/econ_gov/server.py")


def test_month_back_wraps_years():
    from datetime import date
    assert eg._month_back(date(2026, 9, 1), 12) == (2025, 9)
    assert eg._month_back(date(2026, 1, 1), 1) == (2025, 12)


def test_yoy_is_matched_by_date_not_row_offset():
    """Regression: FRED never published Oct-2025 CPI, so '12 rows back' compared Sep-2026 with Aug-2025."""
    rows = [("2025-08-01", 100.0), ("2025-09-01", 102.0), ("2025-11-01", 103.0), ("2025-12-01", 104.0),
            ("2026-01-01", 105.0), ("2026-02-01", 106.0), ("2026-03-01", 107.0), ("2026-04-01", 108.0),
            ("2026-05-01", 109.0), ("2026-06-01", 110.0), ("2026-07-01", 111.0), ("2026-08-01", 112.0),
            ("2026-09-01", 114.24)]
    assert len(rows) - 1 - 12 == 0 and rows[0][0] == "2025-08-01"   # the row-offset trap lands on Aug-2025
    assert eg._pct(rows, 12) == pytest.approx(12.0, abs=0.01)         # 114.24 / 102.0 (Sep-2025) - 1
    assert eg._pct(rows, 1) == pytest.approx((114.24 / 112.0 - 1) * 100, abs=0.01)


def test_yoy_none_when_comparison_month_missing():
    assert eg._pct([("2025-11-01", 1.0), ("2026-10-01", 2.0)], 12) is None  # Oct-2025 absent
    assert eg._pct([], 12) is None


def test_article_text_strips_markup_and_scripts():
    html = '<div id="article" class="x"><script>evil()</script><p>Hello <b>world</b></p><style>p{}</style></div>'
    t = eg._article_text(html)
    assert "Hello world" in t and "evil" not in t and "<" not in t and "class" not in t


def test_fed_documents_only_from_federalreserve_gov():
    for url in ("https://evil.example.com/x", "http://www.federalreserve.gov/x.htm", "file:///etc/passwd"):
        out = json.loads(eg.fed_get_document(url))
        assert "error" in out["data"]
    assert "PDF" in json.loads(eg.fed_get_document("https://www.federalreserve.gov/a.pdf"))["data"]["error"]


def test_outputs_are_wrapped_as_untrusted():
    out = json.loads(eg.fed_get_document("https://evil.example.com/x"))
    assert out["_econ_gov_security"]["trust"] == "untrusted_tool_output"


def test_fetch_rejects_unlisted_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "CACHE", tmp_path)
    with pytest.raises(ValueError):
        eg._get("https://evil.example.com/steal", ttl=0)


def test_api_key_never_appears_in_fetch_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "CACHE", tmp_path)
    monkeypatch.setattr(eg.time, "sleep", lambda s: None)
    monkeypatch.setattr(eg.httpx, "get", lambda *a, **k: httpx.Response(500, request=httpx.Request("GET", a[0])))
    with pytest.raises(RuntimeError) as e:
        eg._get("https://api.stlouisfed.org/fred/series?series_id=X&api_key=SUPERSECRETKEY123", ttl=0, attempts=1)
    assert "SUPERSECRETKEY123" not in str(e.value) and "api_key=***" in str(e.value)


def test_vintage_tools_explain_missing_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    for out in (eg.fred_as_of("PAYEMS", "2025-06-30"), eg.fred_first_release("PAYEMS", "2025-01-01")):
        assert "FRED_API_KEY" in json.loads(out)["data"]["error"]


def test_series_ids_are_validated(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "k")
    assert "bad series id" in json.loads(eg.fred_as_of("../etc", "2025-01-01"))["data"]["error"]
