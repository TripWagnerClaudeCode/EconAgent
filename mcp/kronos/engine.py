"""Kronos engine: data fetch, model lifecycle, sampled forecasts, walk-forward evaluation.

Kept separate from the MCP wrapper (server.py) so scripts and tests can import it directly.
"""
from __future__ import annotations

import gc
import json
import math
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KRONOS_REPO = ROOT / "vendor" / "Kronos"
CACHE = ROOT / "data" / "cache" / "kronos"
CACHE.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(KRONOS_REPO))

# Pinned to the revisions the upstream regression test uses, so weights cannot change under us.
MODEL_ID, MODEL_REV = "NeoQuasar/Kronos-small", "901c26c1332695a2a8f243eb2f37243a37bea320"
TOKENIZER_ID, TOKENIZER_REV = "NeoQuasar/Kronos-Tokenizer-base", "0e0117387f39004a9016484a186a908917e22426"
MAX_CONTEXT = 512
IDLE_UNLOAD_SECONDS = 300  # 8 GB machine: free the model when idle

INTERVALS = {  # label -> (yfinance interval, yfinance period, bar minutes or None for daily, bars/year)
    "1d": ("1d", "5y", None, 252),
    "1h": ("60m", "730d", 60, 252 * 7),
    "30m": ("30m", "60d", 30, 252 * 13),
    "15m": ("15m", "60d", 15, 252 * 26),
}

_lock = threading.RLock()
_predictor = None
_last_used = 0.0
_unload_timer: threading.Timer | None = None


# ---------------------------------------------------------------- model lifecycle
def _device() -> str:
    forced = os.environ.get("KRONOS_DEVICE")
    if forced:
        return forced
    # CPU beat MPS for these small batches (4.1s vs 7.6s for 8 paths) and is what upstream's regression test uses.
    return "cpu"


def get_predictor():
    """Load (once) and return the shared KronosPredictor; schedules an idle unload."""
    global _predictor, _last_used
    with _lock:
        if _predictor is None:
            from model import Kronos, KronosPredictor, KronosTokenizer
            tok = KronosTokenizer.from_pretrained(TOKENIZER_ID, revision=TOKENIZER_REV)
            mdl = Kronos.from_pretrained(MODEL_ID, revision=MODEL_REV)
            _predictor = KronosPredictor(mdl, tok, device=_device(), max_context=MAX_CONTEXT)
        _last_used = time.time()
        _schedule_unload()
        return _predictor


def _schedule_unload() -> None:
    global _unload_timer
    if _unload_timer:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(IDLE_UNLOAD_SECONDS + 1, unload_if_idle)
    _unload_timer.daemon = True
    _unload_timer.start()


def unload_if_idle() -> bool:
    global _predictor
    with _lock:
        if _predictor is not None and time.time() - _last_used >= IDLE_UNLOAD_SECONDS:
            _predictor = None
            gc.collect()
            try:
                import torch
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            return True
    return False


# ---------------------------------------------------------------- data
def history(symbol: str, interval: str = "1d") -> pd.DataFrame:
    """Full available OHLCV history (split/dividend adjusted) with tz-naive exchange-local `timestamps` column."""
    import yfinance as yf
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {sorted(INTERVALS)}")
    yf_int, period, _, _ = INTERVALS[interval]
    raw = yf.Ticker(symbol).history(period=period, interval=yf_int, auto_adjust=True)
    if raw is None or raw.empty:
        raise ValueError(f"no price data returned for {symbol!r}")
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    df.index = idx.normalize() if interval == "1d" else idx
    df = df[~df.index.duplicated()].dropna()
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df.index.name = "timestamps"
    return df.reset_index()


def fetch_bars(symbol: str, interval: str = "1d", lookback: int = 400) -> pd.DataFrame:
    """Most recent `lookback` bars (capped at Kronos's 512-bar context)."""
    return history(symbol, interval).tail(min(lookback, MAX_CONTEXT)).reset_index(drop=True)


def future_timestamps(last: pd.Timestamp, interval: str, n: int) -> pd.Series:
    """Next n bar timestamps after `last`. Weekdays + US federal holidays only (NYSE's Good Friday is not
    modelled, so a daily horizon crossing it is off by one bar; acceptable for a ~10 bar forecast)."""
    from pandas.tseries.holiday import USFederalHolidayCalendar
    from pandas.tseries.offsets import CustomBusinessDay
    cbd = CustomBusinessDay(calendar=USFederalHolidayCalendar())
    mins = INTERVALS[interval][2]
    if mins is None:
        return pd.Series(pd.date_range(last + cbd, periods=n, freq=cbd))
    # yfinance intraday bars are stamped at bar open: 9:30, 9:30+step, ... < 16:00
    slots = []
    t = 9 * 60 + 30
    while t < 16 * 60:
        slots.append(pd.Timedelta(minutes=t))
        t += mins
    out, day = [], last.normalize()
    after = last - day
    while len(out) < n:
        if day == last.normalize():
            cands = [day + s for s in slots if s > after]
        else:
            cands = [day + s for s in slots]
        out.extend(cands)
        day = day + cbd
    return pd.Series(out[:n])


# ---------------------------------------------------------------- forecasting
def sample_paths(df: pd.DataFrame, interval: str, pred_len: int, samples: int,
                 T: float = 1.0, top_p: float = 0.9) -> tuple[np.ndarray, pd.Series]:
    """Run `samples` independent stochastic forecasts; returns closes array (samples, pred_len) and timestamps."""
    pred = get_predictor()
    x_ts = pd.Series(pd.to_datetime(df["timestamps"]).values)
    y_ts = future_timestamps(pd.Timestamp(x_ts.iloc[-1]), interval, pred_len)
    x_df = df[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    with _lock:
        outs = pred.predict_batch(
            df_list=[x_df] * samples, x_timestamp_list=[x_ts] * samples, y_timestamp_list=[y_ts] * samples,
            pred_len=pred_len, T=T, top_k=0, top_p=top_p, sample_count=1, verbose=False)
    closes = np.stack([o["close"].to_numpy(dtype=float) for o in outs])
    return closes, y_ts


def forecast(symbol: str, interval: str = "1d", lookback: int = 400, pred_len: int = 10, samples: int = 12,
             T: float = 1.0, top_p: float = 0.9) -> dict:
    pred_len = max(1, min(pred_len, 60))
    samples = max(4, min(samples, 30))
    df = fetch_bars(symbol, interval, lookback)
    if len(df) < 60:
        raise ValueError(f"only {len(df)} bars available for {symbol}; need at least 60")
    last_close = float(df["close"].iloc[-1])
    t0 = time.time()
    closes, y_ts = sample_paths(df, interval, pred_len, samples, T, top_p)
    rets = closes / last_close - 1
    final = rets[:, -1] * 100
    pct = lambda a, q: float(np.percentile(a, q))  # noqa: E731
    bars_per_year = INTERVALS[interval][3]
    hist_lr = np.log(df["close"]).diff().dropna()
    path_lr = np.diff(np.log(np.concatenate([np.full((samples, 1), last_close), closes], axis=1)), axis=1)
    run_max = np.maximum.accumulate(np.concatenate([np.ones((samples, 1)), 1 + rets], axis=1), axis=1)
    drawdown = ((np.concatenate([np.ones((samples, 1)), 1 + rets], axis=1) / run_max) - 1).min(axis=1) * 100
    track = track_record(symbol, interval)
    wz = float((last_close - df["close"].mean()) / (df["close"].std() + 1e-9))
    return {
        "symbol": symbol.upper(), "interval": interval, "model": MODEL_ID, "device": _device(),
        "bars_used": len(df), "history_end": str(df["timestamps"].iloc[-1]), "last_close": round(last_close, 4),
        "pred_len": pred_len, "samples": samples, "runtime_s": round(time.time() - t0, 1),
        "horizon": [{"t": str(t), "median": round(float(np.median(closes[:, i])), 4),
                     "p10": round(pct(closes[:, i], 10), 4), "p90": round(pct(closes[:, i], 90), 4)}
                    for i, t in enumerate(y_ts)],
        "final_return_pct": {"median": round(pct(final, 50), 2), "p10": round(pct(final, 10), 2),
                             "p90": round(pct(final, 90), 2), "prob_up": round(float((final > 0).mean()), 2)},
        "path_max_drawdown_pct_median": round(float(np.median(drawdown)), 2),
        "volatility_annualized_pct": {
            "forecast": round(float(path_lr.std(axis=1, ddof=1).mean() * math.sqrt(bars_per_year) * 100), 1),
            "trailing_realized": round(float(hist_lr.tail(60).std(ddof=1) * math.sqrt(bars_per_year) * 100), 1)},
        "window_zscore": round(wz, 2),
        "bias_warning": (
            f"Last price is {wz:+.1f} std from its {len(df)}-bar window mean. Kronos normalises each window by its own "
            "mean/std and was observed (AAPL, 14 past windows) to predict a snap-back toward that mean: avg predicted "
            "5d return -8.8% vs +0.7% realised. Absolute returns and volatility from this tool are biased for trending "
            "assets; use kronos_rank (relative to peers) instead." if abs(wz) > 1.0 else None),
        "track_record": track,
        "how_to_read": ("Bands are P10-P90 across independent sampled paths, i.e. the model's own uncertainty, "
                        "not a calibrated confidence interval. Treat as one input among several, never as a signal alone."),
    }


# ---------------------------------------------------------------- walk-forward evaluation
def _bt_file() -> Path:
    return CACHE / "backtests.json"


def _log_run(res: dict) -> None:
    """Append-only audit trail: backtests.json keeps only the latest per key, this keeps every run."""
    with open(CACHE / "backtest_log.jsonl", "a") as fh:
        fh.write(json.dumps(res) + "\n")


def track_record(symbol: str, interval: str) -> dict:
    """Latest stored walk-forward verdict for this symbol/interval, else the best available evidence."""
    f = _bt_file()
    if not f.exists():
        return {"status": "UNVALIDATED", "note": "No walk-forward test on file. Run kronos_backtest before relying on Kronos."}
    data = json.loads(f.read_text())
    key = f"{symbol.upper()}|{interval}"
    hits = [v for k, v in data.items() if k.startswith(key)]  # RANK|... entries never match a ticker prefix
    if not hits:
        n = len(data)
        return {"status": "UNVALIDATED", "note": f"No walk-forward test for {symbol.upper()} ({n} other test(s) on file)."}
    h = sorted(hits, key=lambda v: v["run_at"])[-1]
    return {k: h[k] for k in ("status", "run_at", "n_origins", "horizon", "verdict_reason")}


def walk_forward(symbol: str, interval: str = "1d", horizon: int = 5, origins: int = 20, samples: int = 6,
                 lookback: int = 400) -> dict:
    """Out-of-sample test: forecast from past origins, compare with what actually happened.

    Baselines: zero-change (random walk) and trailing-mean drift. Kronos is only marked EARNED_TRUST if it
    beats the random walk on MAE *and* its directional accuracy is significantly better than a coin flip.
    """
    origins = max(8, min(origins, 40))
    horizon = max(1, min(horizon, 30))
    lookback = min(lookback, MAX_CONTEXT)
    raw = history(symbol, interval)
    need = lookback + origins * horizon + horizon
    if len(raw) < need:
        origins = max(8, (len(raw) - lookback - horizon) // horizon)
        need = lookback + origins * horizon + horizon
        if len(raw) < need:
            raise ValueError(f"not enough history for {symbol} at {interval}: {len(raw)} bars")
    raw = raw.tail(need).reset_index(drop=True)
    rows = []
    for k in range(origins):
        end = lookback + k * horizon  # exclusive end of the context window
        ctx = raw.iloc[end - lookback:end]
        closes, _ = sample_paths(ctx, interval, horizon, samples)
        c0 = float(ctx["close"].iloc[-1])
        pred_ret = float(np.median(closes[:, -1]) / c0 - 1)
        real_ret = float(raw["close"].iloc[end + horizon - 1] / c0 - 1)
        drift = float(np.log(ctx["close"]).diff().dropna().mean() * horizon)
        rows.append({"pred": pred_ret, "real": real_ret, "drift": drift})
    d = pd.DataFrame(rows)
    n = len(d)
    mae_k = float((d.pred - d.real).abs().mean())
    mae_rw = float(d.real.abs().mean())
    mae_dr = float((d.drift - d.real).abs().mean())
    nonzero = d[d.real != 0]
    acc = float((np.sign(nonzero.pred) == np.sign(nonzero.real)).mean())
    z = (acc - 0.5) / math.sqrt(0.25 / max(len(nonzero), 1))
    ic = float(d.pred.rank().corr(d.real.rank())) if d.pred.nunique() > 1 else float("nan")
    beats_rw = mae_k < mae_rw
    significant = z > 1.64 and n >= 20
    if beats_rw and significant:
        status, why = "EARNED_TRUST", "beat the random-walk baseline on MAE and direction was significantly better than a coin flip"
    elif beats_rw or significant:
        status, why = "WEAK_EVIDENCE", "beat the baseline on only one of MAE / directional significance"
    else:
        status, why = "NO_EDGE", "did not beat the random-walk baseline and direction was not distinguishable from a coin flip"
    res = {
        "symbol": symbol.upper(), "interval": interval, "horizon": horizon, "n_origins": n, "samples_per_origin": samples,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "directional_accuracy": round(acc, 3), "accuracy_z_vs_coinflip": round(z, 2),
        "rank_ic": None if math.isnan(ic) else round(ic, 3),
        "mae_kronos_pct": round(mae_k * 100, 3), "mae_random_walk_pct": round(mae_rw * 100, 3),
        "mae_trailing_drift_pct": round(mae_dr * 100, 3), "mae_ratio_vs_random_walk": round(mae_k / mae_rw, 3) if mae_rw else None,
        "status": status, "verdict_reason": why,
        "caveats": ["Overlapping-free origins, but n is small: treat as a screening test, not proof.",
                    "Frictionless: no transaction costs, slippage or taxes are modelled."],
    }
    f = _bt_file()
    store = json.loads(f.read_text()) if f.exists() else {}
    store[f"{symbol.upper()}|{interval}|{horizon}"] = res
    f.write_text(json.dumps(store, indent=1))
    _log_run(res)
    return res


# ---------------------------------------------------------------- cross-sectional ranking (the paper's RankIC setting)
def _median_final_return(ctx: pd.DataFrame, interval: str, horizon: int, samples: int) -> float:
    closes, _ = sample_paths(ctx, interval, horizon, samples)
    return float(np.median(closes[:, -1]) / float(ctx["close"].iloc[-1]) - 1)


def rank(symbols: list[str], interval: str = "1d", horizon: int = 5, samples: int = 6, lookback: int = 400) -> dict:
    """Rank symbols by Kronos's predicted return *relative to the peer median*.

    Subtracting the cross-sectional median cancels the common mean-reversion bias, leaving only the
    differences between names, which is what the paper's RankIC measures.
    """
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    if not 3 <= len(symbols) <= 15:
        raise ValueError("give between 3 and 15 distinct symbols (ranking needs peers)")
    raw, errors = {}, {}
    for sym in symbols:
        try:
            df = fetch_bars(sym, interval, lookback)
            if len(df) < 60:
                raise ValueError(f"only {len(df)} bars")
            raw[sym] = _median_final_return(df, interval, horizon, samples) * 100
        except Exception as e:  # noqa: BLE001
            errors[sym] = str(e)[:100]
    if len(raw) < 3:
        raise ValueError(f"fewer than 3 symbols usable: {errors}")
    med = float(np.median(list(raw.values())))
    sd = float(np.std(list(raw.values()), ddof=1)) or 1.0
    ranked = sorted(raw.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "interval": interval, "horizon_bars": horizon, "peer_median_raw_return_pct": round(med, 2),
        "ranking": [{"rank": i + 1, "symbol": k, "relative_return_pct": round(v - med, 2),
                     "relative_zscore": round((v - med) / sd, 2), "raw_return_pct_biased": round(v, 2)}
                    for i, (k, v) in enumerate(ranked)],
        "errors": errors, "track_record": rank_track_record(interval, horizon),
        "how_to_read": ("Order matters, magnitude does not: use only the rank / relative_zscore, not the raw return "
                        "(biased). A top-ranked name is one Kronos expects to outperform its peers, not to go up."),
    }


def rank_walk_forward(symbols: list[str], interval: str = "1d", horizon: int = 5, origins: int = 12,
                      samples: int = 4, lookback: int = 400) -> dict:
    """Out-of-sample cross-sectional test: at each past origin, rank names by prediction, correlate with realised returns.

    Reports mean Spearman IC across origins and its t-stat. EARNED_TRUST needs mean IC > 0.03 and t > 1.64.
    """
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    if not 5 <= len(symbols) <= 15:
        raise ValueError("give between 5 and 15 distinct symbols")
    origins = max(8, min(origins, 30))
    lookback = min(lookback, MAX_CONTEXT)
    hist = {}
    for sym in symbols:
        try:
            hist[sym] = history(sym, interval)
        except Exception:  # noqa: BLE001
            continue
    need = lookback + origins * horizon + horizon
    hist = {k: v for k, v in hist.items() if len(v) >= need}
    if len(hist) < 5:
        raise ValueError(f"need >=5 symbols with {need}+ bars; got {len(hist)}")
    ics = []
    for k in range(origins):
        preds, reals = {}, {}
        for sym, h in hist.items():
            h = h.tail(need).reset_index(drop=True)
            end = lookback + k * horizon
            ctx = h.iloc[end - lookback:end]
            c0 = float(ctx["close"].iloc[-1])
            preds[sym] = _median_final_return(ctx, interval, horizon, samples)
            reals[sym] = float(h["close"].iloc[end + horizon - 1] / c0 - 1)
        pr, re_ = pd.Series(preds), pd.Series(reals)
        if pr.nunique() > 1 and re_.nunique() > 1:
            ics.append(float(pr.rank().corr(re_.rank())))  # Spearman = Pearson on ranks (avoids a scipy dependency)
    n = len(ics)
    if n < 6:
        raise ValueError(f"only {n} usable origins")
    mean_ic, sd_ic = float(np.mean(ics)), float(np.std(ics, ddof=1))
    t = mean_ic / (sd_ic / math.sqrt(n)) if sd_ic else float("nan")
    if mean_ic > 0.03 and t > 1.64 and n >= 20:
        status, why = "EARNED_TRUST", f"mean rank IC {mean_ic:.3f} with t={t:.2f} across {n} origins"
    elif mean_ic > 0.03 and t > 1.64:
        status, why = "PROMISING_SMALL_SAMPLE", f"mean rank IC {mean_ic:.3f}, t={t:.2f}, but only {n} origins (<20): rerun with more"
    elif mean_ic > 0 and t > 1.0:
        status, why = "WEAK_EVIDENCE", f"positive rank IC {mean_ic:.3f} but t={t:.2f} is below the 1.64 bar"
    else:
        status, why = "NO_EDGE", f"mean rank IC {mean_ic:.3f}, t={t:.2f}: not distinguishable from zero"
    res = {"kind": "cross_sectional", "interval": interval, "horizon": horizon, "n_origins": n,
           "symbols": sorted(hist), "mean_rank_ic": round(mean_ic, 4), "ic_t_stat": round(t, 2),
           "ic_hit_rate": round(float(np.mean([i > 0 for i in ics])), 2),
           "status": status, "verdict_reason": why, "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "caveats": ["Small universe and short window: a screening test, not proof.",
                       "Frictionless: no costs. Survivorship bias: symbols are today's survivors."]}
    f = _bt_file()
    store = json.loads(f.read_text()) if f.exists() else {}
    store[f"RANK|{interval}|{horizon}"] = res
    f.write_text(json.dumps(store, indent=1))
    _log_run(res)
    return res


from track import rank_track_record  # noqa: E402,F401  (torch-free; shared with the web backend)
