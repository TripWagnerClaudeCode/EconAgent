import json

import pytest
import track


def _run(tmp, runs):
    (tmp / "backtest_log.jsonl").write_text("\n".join(json.dumps(r) for r in runs))


def _r(ic, n, t, status="X"):
    return {"kind": "cross_sectional", "interval": "1d", "horizon": 5, "mean_rank_ic": ic, "n_origins": n, "ic_t_stat": t,
            "symbols": list("abcde"), "status": status, "verdict_reason": "r", "run_at": "2026-01-01T00:00:00+00:00"}


def test_no_runs_is_unvalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(track, "CACHE", tmp_path)
    assert track.rank_track_record("1d", 5)["status"] == "UNVALIDATED"


def test_disagreeing_runs_are_inconclusive_not_cherry_picked(tmp_path, monkeypatch):
    monkeypatch.setattr(track, "CACHE", tmp_path)
    _run(tmp_path, [_r(0.20, 14, 2.3, "EARNED_TRUST"), _r(-0.125, 26, -1.69, "NO_EDGE")])
    t = track.rank_track_record("1d", 5)
    assert t["status"] == "INCONCLUSIVE_MIXED" and t["runs"] == 2 and t["total_origins"] == 40
    assert t["pooled_mean_rank_ic"] == pytest.approx((0.2 * 14 - 0.125 * 26) / 40, abs=1e-4)


def test_single_run_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(track, "CACHE", tmp_path)
    _run(tmp_path, [_r(0.05, 30, 1.0, "WEAK_EVIDENCE")])
    assert track.rank_track_record("1d", 5)["status"] == "WEAK_EVIDENCE"


def test_other_horizons_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(track, "CACHE", tmp_path)
    r = _r(0.3, 20, 3.0, "EARNED_TRUST"); r["horizon"] = 20
    _run(tmp_path, [r])
    assert track.rank_track_record("1d", 5)["status"] == "UNVALIDATED"
