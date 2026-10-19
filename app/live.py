"""Live and interactive data endpoints for the UI (no model usage): market bar, quotes, macro tiles, series charts,
FRED search, yield curve, FOMC statement diff, stock history. Everything is cached briefly and fails soft."""
from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Query

import mcp_config

router = APIRouter(prefix="/api")

_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _econ():
    import main  # lazy: main imports this module
    return main.econ()


SYMBOL = re.compile(r"^[A-Za-z0-9^=.\-]{1,12}$")


def _fred(path: str, params: dict, ttl: float):
    """Call the keyed FRED API through econ_gov's cached, polite fetcher. Never leaks the key."""
    econ = _econ()
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise HTTPException(503, "FRED_API_KEY is not set (run config/keys.sh set FRED_API_KEY)")
    q = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
    url = f"https://api.stlouisfed.org/fred/{path}?{q}&api_key={key}&file_type=json"
    try:
        return json.loads(econ._get(url, ttl=ttl, timeout=20, attempts=2))
    except RuntimeError as e:
        raise HTTPException(502, str(e)[:220]) from e


# ----------------------------------------------------------------------------- live market bar
BAR = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow"), ("^VIX", "VIX"), ("^TNX", "10y yield"),
       ("DX-Y.NYB", "Dollar"), ("CL=F", "Oil"), ("GC=F", "Gold"), ("BTC-USD", "Bitcoin")]


def _one_quote(sym: str) -> dict:
    import yfinance as yf
    try:
        fi = yf.Ticker(sym).fast_info
        last, prev = fi["lastPrice"], fi["previousClose"]
        if last is None or prev in (None, 0):
            raise ValueError("no price")
        return {"symbol": sym, "last": float(last), "prev": float(prev), "chg": float(last - prev),
                "chg_pct": float((last / prev - 1) * 100)}
    except Exception:  # noqa: BLE001
        return {"symbol": sym, "error": "no data"}


def quotes(symbols: list[str]) -> list[dict]:
    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(_one_quote, symbols))


def market_clock() -> dict:
    """Open/closed from Alpaca's clock (holiday-aware); falls back to a weekday 9:30-16:00 ET estimate."""
    def go():
        import httpx
        k, s = mcp_config.keychain_get("ALPACA_API_KEY"), mcp_config.keychain_get("ALPACA_SECRET_KEY")
        if k and s:
            try:
                r = httpx.get("https://paper-api.alpaca.markets/v2/clock", timeout=8,
                              headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s})
                if r.status_code == 200:
                    d = r.json()
                    return {"is_open": bool(d["is_open"]), "next_open": d["next_open"], "next_close": d["next_close"], "source": "alpaca"}
            except Exception:  # noqa: BLE001
                pass
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        is_open = now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)
        return {"is_open": is_open, "source": "estimate"}
    return cached("clock", 30, go)


@router.get("/live")
def live():
    def go():
        rows = quotes([s for s, _ in BAR])
        labels = dict(BAR)
        for r in rows:
            r["label"] = labels[r["symbol"]]
        return {"ts": time.time(), "market": market_clock(), "items": rows}
    return cached("live", 8, go)


@router.get("/quotes")
def quotes_ep(symbols: str = Query(max_length=400)):
    syms = [s.upper() for s in symbols.split(",") if s.strip()]
    if not syms or len(syms) > 30 or not all(SYMBOL.match(s) for s in syms):
        raise HTTPException(400, "give 1-30 valid symbols")
    key = "q:" + ",".join(sorted(syms))
    return {"ts": time.time(), "quotes": cached(key, 8, lambda: quotes(syms))}


# ----------------------------------------------------------------------------- macro tiles
# (series id, label, units transform, display unit, decimals, cadence)
CATALOG = {
    "Policy & rates": [
        ("DFF", "Fed funds (effective)", "lin", "%", 2, "daily"), ("DGS2", "2-year Treasury", "lin", "%", 2, "daily"),
        ("DGS10", "10-year Treasury", "lin", "%", 2, "daily"), ("T10Y2Y", "Curve: 10y minus 2y", "lin", " pts", 2, "daily"),
        ("T10Y3M", "Curve: 10y minus 3m", "lin", " pts", 2, "daily")],
    "Inflation": [
        ("CPIAUCSL", "CPI, year over year", "pc1", "%", 2, "monthly"), ("CPILFESL", "Core CPI, year over year", "pc1", "%", 2, "monthly"),
        ("PCEPILFE", "Core PCE, year over year", "pc1", "%", 2, "monthly"), ("T10YIE", "10-yr breakeven inflation", "lin", "%", 2, "daily")],
    "Labor & growth": [
        ("UNRATE", "Unemployment rate", "lin", "%", 1, "monthly"), ("PAYEMS", "Payrolls, monthly change", "chg", "k", 0, "monthly"),
        ("ICSA", "Initial jobless claims", "lin", "", 0, "weekly"), ("A191RL1Q225SBEA", "Real GDP growth (annualized)", "lin", "%", 1, "quarterly"),
        ("UMCSENT", "Consumer sentiment", "lin", "", 1, "monthly")],
    "Credit & conditions": [
        ("BAMLH0A0HYM2", "High-yield spread", "lin", "%", 2, "daily"), ("BAMLC0A0CM", "Investment-grade spread", "lin", "%", 2, "daily"),
        ("NFCI", "Financial conditions (NFCI)", "lin", "", 2, "weekly")],
}


def _obs(sid: str, start: str, units: str = "lin", ttl: float = 1800) -> list[tuple[str, float]]:
    d = _fred("series/observations", {"series_id": sid, "observation_start": start, "units": units}, ttl)
    out = []
    for o in d.get("observations", []):
        try:
            out.append((o["date"], float(o["value"])))
        except (ValueError, KeyError):
            continue
    return out


def _tile(item: tuple, group: str) -> dict:
    sid, label, units, unit, dec, cadence = item
    base = {"id": sid, "label": label, "group": group, "unit": unit, "decimals": dec, "cadence": cadence, "units": units}
    try:
        start = (date.today() - timedelta(days=800)).isoformat()
        rows = _obs(sid, start, units)
        if len(rows) < 2:
            raise ValueError("no data")
        step = max(1, len(rows) // 70)
        spark = [v for _, v in rows[::step]] + ([rows[-1][1]] if (len(rows) - 1) % step else [])
        return {**base, "latest": {"date": rows[-1][0], "value": rows[-1][1]},
                "prev": {"date": rows[-2][0], "value": rows[-2][1]}, "spark": spark}
    except HTTPException as e:
        return {**base, "error": str(e.detail)[:120]}
    except Exception as e:  # noqa: BLE001
        return {**base, "error": str(e)[:120]}


@router.get("/macro/tiles")
def macro_tiles():
    def go():
        jobs = [(it, g) for g, items in CATALOG.items() for it in items]
        with ThreadPoolExecutor(max_workers=4) as ex:
            tiles = list(ex.map(lambda a: _tile(*a), jobs))
        return {"as_of": date.today().isoformat(), "groups": list(CATALOG), "tiles": tiles}
    return cached("tiles", 900, go)


# ----------------------------------------------------------------------------- interactive series
UNITS = {"lin": "Level", "pc1": "Year over year %", "chg": "Change from prior", "pch": "% change from prior", "ch1": "Change from year ago"}
RANGES = {"1y": 365, "2y": 730, "5y": 1826, "10y": 3652, "max": None}


@router.get("/series/{series_id}")
def series(series_id: str, range: str = "5y", units: str = "lin"):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,30}", series_id):
        raise HTTPException(400, "bad series id")
    if range not in RANGES or units not in UNITS:
        raise HTTPException(400, "bad range or units")
    meta = cached(f"meta:{series_id}", 86400, lambda: _fred("series", {"series_id": series_id}, 86400).get("seriess", [{}])[0])
    start = (date.today() - timedelta(days=RANGES[range])).isoformat() if RANGES[range] else "1776-07-04"
    rows = _obs(series_id.upper(), start, units, ttl=900)
    step = max(1, len(rows) // 700)
    pts = rows[::step] + ([rows[-1]] if rows and (len(rows) - 1) % step else [])
    return {"id": series_id.upper(), "title": meta.get("title", series_id), "frequency": meta.get("frequency_short"),
            "native_units": meta.get("units"), "last_updated": meta.get("last_updated"), "units": units,
            "units_label": UNITS[units], "range": range, "points": [[d, v] for d, v in pts]}


@router.get("/fred/search")
def fred_search(q: str = Query(min_length=2, max_length=80)):
    d = _fred("series/search", {"search_text": q, "limit": 12, "order_by": "popularity", "sort_order": "desc"}, 3600)
    return [{"id": s["id"], "title": s["title"], "frequency": s.get("frequency_short"), "units": s.get("units_short"),
             "last": s.get("observation_end")} for s in d.get("seriess", [])]


# ----------------------------------------------------------------------------- yield curve
MATURITIES = [("DGS1MO", "1m", 1 / 12), ("DGS3MO", "3m", .25), ("DGS6MO", "6m", .5), ("DGS1", "1y", 1), ("DGS2", "2y", 2),
              ("DGS3", "3y", 3), ("DGS5", "5y", 5), ("DGS7", "7y", 7), ("DGS10", "10y", 10), ("DGS20", "20y", 20), ("DGS30", "30y", 30)]


@router.get("/yieldcurve")
def yield_curve():
    def go():
        start = (date.today() - timedelta(days=400)).isoformat()
        with ThreadPoolExecutor(max_workers=4) as ex:
            data = list(ex.map(lambda m: _obs(m[0], start, "lin", ttl=1800), MATURITIES))
        latest = max(rows[-1][0] for rows in data if rows)
        targets = {"now": latest,
                   "1 month ago": (datetime.strptime(latest, "%Y-%m-%d").date() - timedelta(days=30)).isoformat(),
                   "1 year ago": (datetime.strptime(latest, "%Y-%m-%d").date() - timedelta(days=365)).isoformat()}
        curves = []
        for name, t in targets.items():
            vals = []
            used = None
            for (_sid, lab, yrs), rows in zip(MATURITIES, data, strict=True):
                pick = [r for r in rows if r[0] <= t]
                vals.append({"label": lab, "years": yrs, "value": pick[-1][1] if pick else None})
                if pick and used is None:
                    used = pick[-1][0]
            curves.append({"name": name, "date": used or t, "points": vals})
        return {"curves": curves}
    return cached("yc", 1800, go)


# ----------------------------------------------------------------------------- FOMC diff
@router.get("/fed/diff")
def fed_diff():
    def go():
        econ = _econ()
        docs = json.loads(econ.fed_list_documents("statements", 2))["data"]["documents"]
        if len(docs) < 2:
            return {"error": "need two statements"}
        diff = json.loads(econ.fed_compare_statements(docs[1]["url"], docs[0]["url"]))["data"]
        changes = [c for c in diff["changes"] if "Implementation Note" not in ((c.get("old") or "") + (c.get("new") or ""))]
        return {"old": docs[1], "new": docs[0], "changes": changes, "unchanged": diff["unchanged_sentences"]}
    return cached("feddiff", 6 * 3600, go)


# ----------------------------------------------------------------------------- stock history
HIST = {"1d": ("1d", "5m"), "5d": ("5d", "15m"), "1mo": ("1mo", "1d"), "6mo": ("6mo", "1d"), "1y": ("1y", "1d"), "5y": ("5y", "1wk")}


@router.get("/history/{symbol}")
def history(symbol: str, range: str = "6mo"):
    symbol = symbol.upper()
    if not SYMBOL.match(symbol) or range not in HIST:
        raise HTTPException(400, "bad symbol or range")

    def go():
        import yfinance as yf
        period, interval = HIST[range]
        h = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if h is None or h.empty:
            raise HTTPException(404, f"no data for {symbol}")
        if getattr(h.index, "tz", None) is not None:
            h.index = h.index.tz_convert("America/New_York")      # tooltips should read in market (Eastern) time
        c = h["Close"].dropna()
        fmt = "%Y-%m-%dT%H:%M" if interval not in ("1d", "1wk") else "%Y-%m-%d"
        return {"symbol": symbol, "range": range, "interval": interval,
                "points": [[ts.strftime(fmt), round(float(v), 4)] for ts, v in c.items()]}
    return cached(f"h:{symbol}:{range}", 120 if range in ("1d", "5d") else 600, go)
