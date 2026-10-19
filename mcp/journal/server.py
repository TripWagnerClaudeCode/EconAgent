"""journal MCP server: a prediction log so the agent's calls get scored against reality.

Every recommendation is logged with a thesis, levels, horizon and a stated probability. Closing an idea
records the outcome; `journal_calibration` then reports hit rate and Brier score by confidence bucket, so
overconfidence shows up in numbers instead of staying invisible.

Storage: SQLite at ~/EconAgent/journal/journal.db (local only).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DB = Path(os.environ.get("ECONAGENT_JOURNAL_DB") or Path(__file__).resolve().parents[2] / "journal" / "journal.db")
DB.parent.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("journal")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  symbol TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long','short','avoid')),
  thesis TEXT NOT NULL,
  entry_price REAL NOT NULL,
  target_price REAL,
  stop_price REAL,
  horizon_days INTEGER NOT NULL,
  probability REAL NOT NULL CHECK (probability BETWEEN 0 AND 1),
  expected_return_pct REAL,
  max_drawdown_pct REAL,
  kronos_used INTEGER NOT NULL DEFAULT 0,
  sources TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  closed_at TEXT,
  exit_price REAL,
  outcome TEXT CHECK (outcome IN ('hit','miss','partial')),
  realized_return_pct REAL,
  note TEXT
);
"""


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(SCHEMA)
    return con


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


@mcp.tool()
def journal_log_idea(symbol: str, direction: str, thesis: str, entry_price: float, horizon_days: int,
                     probability: float, target_price: float | None = None, stop_price: float | None = None,
                     expected_return_pct: float | None = None, max_drawdown_pct: float | None = None,
                     kronos_used: bool = False, sources: list[str] | None = None) -> str:
    """Log a recommendation BEFORE acting on it.

    direction: long | short | avoid. probability: your stated chance (0-1) that the call works by the horizon
    (hits target before stop, or for 'avoid', that the stock underperforms). sources: tool calls / documents
    the thesis rests on. Returns the idea id.
    """
    if direction not in ("long", "short", "avoid"):
        return json.dumps({"error": "direction must be long | short | avoid"})
    if not 0 <= probability <= 1:
        return json.dumps({"error": "probability must be between 0 and 1"})
    if horizon_days < 1:
        return json.dumps({"error": "horizon_days must be >= 1"})
    with _db() as con:
        cur = con.execute(
            """INSERT INTO ideas (created_at, symbol, direction, thesis, entry_price, target_price, stop_price,
               horizon_days, probability, expected_return_pct, max_drawdown_pct, kronos_used, sources)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), symbol.upper(), direction, thesis,
             entry_price, target_price, stop_price, horizon_days, probability, expected_return_pct,
             max_drawdown_pct, int(kronos_used), json.dumps(sources or [])))
        return json.dumps({"id": cur.lastrowid, "logged": symbol.upper(), "review_on":
                           (datetime.now(timezone.utc) + timedelta(days=horizon_days)).date().isoformat()})


@mcp.tool()
def journal_list(status: str = "open", symbol: str | None = None, limit: int = 25) -> str:
    """List ideas. status: open | closed | due | all. 'due' = open ideas past their horizon (needs closing)."""
    q, args = "SELECT * FROM ideas", []
    where = []
    if status in ("open", "closed"):
        where.append("status = ?"); args.append(status)
    elif status == "due":
        where.append("status = 'open' AND julianday('now') - julianday(created_at) >= horizon_days")
    elif status != "all":
        return json.dumps({"error": "status must be open | closed | due | all"})
    if symbol:
        where.append("symbol = ?"); args.append(symbol.upper())
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with _db() as con:
        return json.dumps(_rows(con.execute(q, args).fetchall()))


@mcp.tool()
def journal_close_idea(idea_id: int, exit_price: float, outcome: str, note: str = "") -> str:
    """Close an idea with the actual result.

    outcome: hit (target reached / thesis right) | miss (stop hit / thesis wrong) | partial.
    realized_return_pct is computed from entry/exit and direction (short and avoid invert the sign).
    """
    if outcome not in ("hit", "miss", "partial"):
        return json.dumps({"error": "outcome must be hit | miss | partial"})
    with _db() as con:
        row = con.execute("SELECT * FROM ideas WHERE id = ?", (idea_id,)).fetchone()
        if not row:
            return json.dumps({"error": f"no idea {idea_id}"})
        if row["status"] == "closed":
            return json.dumps({"error": f"idea {idea_id} is already closed"})
        move = (exit_price / row["entry_price"] - 1) * 100
        realized = move if row["direction"] == "long" else -move
        con.execute("""UPDATE ideas SET status='closed', closed_at=?, exit_price=?, outcome=?,
                       realized_return_pct=?, note=? WHERE id=?""",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), exit_price, outcome,
                     round(realized, 2), note, idea_id))
        return json.dumps({"id": idea_id, "outcome": outcome, "realized_return_pct": round(realized, 2)})


@mcp.tool()
def journal_calibration() -> str:
    """Scorecard over closed ideas: hit rate, Brier score, and calibration by stated-probability bucket.

    Brier: mean((probability - outcome)^2), outcome=1 for hit, 0.5 for partial, 0 for miss. Lower is better;
    always predicting 50% scores 0.25. A bucket whose hit rate is far below its stated probability means the
    agent is overconfident there. Also reports whether Kronos-assisted ideas did better.
    """
    with _db() as con:
        rows = _rows(con.execute("SELECT * FROM ideas WHERE status='closed'").fetchall())
        n_open = con.execute("SELECT COUNT(*) FROM ideas WHERE status='open'").fetchone()[0]
    if not rows:
        return json.dumps({"closed": 0, "open": n_open, "note": "No closed ideas yet; nothing to score."})
    score = {"hit": 1.0, "partial": 0.5, "miss": 0.0}
    brier = sum((r["probability"] - score[r["outcome"]]) ** 2 for r in rows) / len(rows)
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        lo = min(int(r["probability"] * 10) * 10, 90)
        buckets.setdefault(f"{lo}-{lo + 10}%", []).append(r)
    cal = [{"bucket": b, "n": len(v),
            "stated_avg": round(sum(x["probability"] for x in v) / len(v), 3),
            "actual_rate": round(sum(score[x["outcome"]] for x in v) / len(v), 3)}
           for b, v in sorted(buckets.items())]

    def avg_ret(sel):
        v = [x["realized_return_pct"] for x in sel if x["realized_return_pct"] is not None]
        return round(sum(v) / len(v), 2) if v else None
    k = [r for r in rows if r["kronos_used"]]
    nk = [r for r in rows if not r["kronos_used"]]
    return json.dumps({
        "closed": len(rows), "open": n_open, "brier": round(brier, 4), "brier_baseline_always_50pct": 0.25,
        "hit_rate": round(sum(score[r["outcome"]] for r in rows) / len(rows), 3),
        "avg_realized_return_pct": avg_ret(rows), "calibration": cal,
        "kronos_assisted": {"n": len(k), "avg_return_pct": avg_ret(k)},
        "not_kronos": {"n": len(nk), "avg_return_pct": avg_ret(nk)},
        "caution": "Small samples are noise; do not read much into buckets with n < 10."})


if __name__ == "__main__":
    mcp.run()
