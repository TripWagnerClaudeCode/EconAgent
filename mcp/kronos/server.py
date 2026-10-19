"""kronos MCP server: Kronos (AAAI 2026 K-line foundation model) as an analyst tool.

Read skills/kronos-forecast/SKILL.md for how to use the output. Short version: rank peers with kronos_rank;
treat absolute return / volatility numbers as biased for trending assets; check track_record before citing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp.server.fastmcp import FastMCP  # noqa: E402

import engine  # noqa: E402

mcp = FastMCP("kronos")


def _run(fn, *a, **kw) -> str:
    try:
        return json.dumps(fn(*a, **kw), default=str)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"})


@mcp.tool()
def kronos_forecast(symbol: str, interval: str = "1d", pred_len: int = 10, samples: int = 12, lookback: int = 400) -> str:
    """Sampled price-path forecast for one ticker: median and P10/P90 close per future bar, final-return
    distribution, path drawdown, and forecast vs realised volatility.

    interval: 1d | 1h | 30m | 15m (intraday history is limited by the data source). pred_len <= 60 bars.
    CAUTION: check `bias_warning` and `track_record` in the result. For assets far from their window mean the
    absolute return and volatility are biased; prefer kronos_rank for decisions.
    """
    return _run(engine.forecast, symbol, interval, lookback, pred_len, samples)


@mcp.tool()
def kronos_volatility(symbol: str, interval: str = "1d", pred_len: int = 10, samples: int = 12) -> str:
    """Kronos's volatility view for one ticker (annualised %), next to trailing realised volatility.

    The same bias caveat as kronos_forecast applies: the forecast figure has run well above realised in tests.
    Use the *ratio* trend across names rather than the level.
    """
    def go():
        r = engine.forecast(symbol, interval, 400, pred_len, samples)
        return {k: r[k] for k in ("symbol", "interval", "history_end", "volatility_annualized_pct",
                                  "window_zscore", "bias_warning", "track_record")}
    return _run(go)


@mcp.tool()
def kronos_rank(symbols: list[str], interval: str = "1d", horizon: int = 5, samples: int = 6) -> str:
    """Rank 3-15 tickers by Kronos's predicted return RELATIVE to the peer median (the paper's RankIC setting).

    Use rank / relative_zscore only. A top rank means 'expected to outperform peers', not 'expected to rise'.
    ~2-5 s per ticker.
    """
    return _run(engine.rank, symbols, interval, horizon, samples)


@mcp.tool()
def kronos_rank_backtest(symbols: list[str], interval: str = "1d", horizon: int = 5, origins: int = 20, samples: int = 4) -> str:
    """Out-of-sample test of the ranking signal on 5-15 tickers: mean Spearman rank IC across past origins,
    its t-stat, and a status (EARNED_TRUST | PROMISING_SMALL_SAMPLE | WEAK_EVIDENCE | NO_EDGE). Slow
    (minutes); results are stored and surfaced in later kronos_rank output. Frictionless, survivorship-biased.
    """
    return _run(engine.rank_walk_forward, symbols, interval, horizon, origins, samples)


@mcp.tool()
def kronos_backtest(symbol: str, interval: str = "1d", horizon: int = 5, origins: int = 20, samples: int = 6) -> str:
    """Single-ticker walk-forward vs a random-walk and drift baseline (directional accuracy, MAE, rank IC).

    Tests the ABSOLUTE forecast, which is the weaker use of Kronos; expect NO_EDGE for trending names.
    """
    return _run(engine.walk_forward, symbol, interval, horizon, origins, samples)


@mcp.tool()
def kronos_track_record() -> str:
    """Every stored walk-forward result (latest per test) so you can cite Kronos's evidence accurately."""
    f = engine._bt_file()
    return json.dumps(json.loads(f.read_text()) if f.exists() else {"note": "no backtests stored yet"})


if __name__ == "__main__":
    mcp.run()
