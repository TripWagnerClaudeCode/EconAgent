"""Torch-free reader for Kronos backtest evidence (used by the engine and by the web backend's status endpoint)."""
from __future__ import annotations

import json
from pathlib import Path

CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "kronos"


def rank_track_record(interval: str, horizon: int) -> dict:
    """Pool EVERY logged cross-sectional run (not just the latest) so a lucky or unlucky single run cannot mislead."""
    f = CACHE / "backtest_log.jsonl"
    runs = []
    if f.exists():
        for line in f.read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("kind") == "cross_sectional" and d.get("interval") == interval and d.get("horizon") == horizon:
                runs.append(d)
    if not runs:
        return {"status": "UNVALIDATED", "note": "No cross-sectional walk-forward on file. Run kronos_rank_backtest."}
    n = sum(r["n_origins"] for r in runs)
    pooled = sum(r["mean_rank_ic"] * r["n_origins"] for r in runs) / n
    signs = {r["mean_rank_ic"] > 0 for r in runs}
    # Pooled t-stat is not computable from summaries alone; weight-averaged IC plus run-to-run agreement is what we can honestly say.
    if len(runs) < 2:
        status, why = runs[-1]["status"], runs[-1]["verdict_reason"]
    elif len(signs) > 1:
        status, why = "INCONCLUSIVE_MIXED", "independent runs disagree in sign: no dependable edge demonstrated"
    elif pooled > 0.03:
        status, why = "WEAK_EVIDENCE", "all runs positive, but each should clear t>1.64 before relying on it"
    else:
        status, why = "NO_EDGE", "pooled IC is not meaningfully positive"
    return {"status": status, "runs": len(runs), "total_origins": n, "pooled_mean_rank_ic": round(pooled, 4),
            "per_run": [{"symbols": len(r["symbols"]), "origins": r["n_origins"], "mean_ic": r["mean_rank_ic"],
                         "t": r["ic_t_stat"], "run_at": r["run_at"][:10]} for r in runs],
            "verdict_reason": why}
