"""EconAgent backend: local-only FastAPI service driving the user's own `claude -p` (subscription login).

Bind 127.0.0.1 only (scripts/serve.py). Requests are accepted only for Host/Origin of this app, so a web page on some
other site cannot drive it. Research mode is the only enabled mode; paper/live ordering is not wired yet.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import claude_runner as cr
import jobs
import mcp_config
import pipeline

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
REPORTS = ROOT / "reports"
DATA = ROOT / "data"
PORT = int(os.environ.get("ECONAGENT_PORT", "8750"))
ENABLED_MODES = ["research"]  # paper/live need the per-order approval flow, which is not built yet

app = FastAPI(title="EconAgent", docs_url=None, redoc_url=None, openapi_url=None)
manager = jobs.JobManager()


# ----------------------------------------------------------------------------- request hygiene
@app.middleware("http")
async def local_only(request: Request, call_next):
    host = (request.headers.get("host") or "").lower()
    if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
        return JSONResponse({"error": "bad host"}, status_code=403)          # DNS-rebinding guard
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin and origin.lower() not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return JSONResponse({"error": "bad origin"}, status_code=403)    # CSRF guard
        if "application/json" not in (request.headers.get("content-type") or "") and request.method != "DELETE":
            return JSONResponse({"error": "JSON only"}, status_code=415)
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
    return resp


# ----------------------------------------------------------------------------- in-process data helpers
def _quiet_http_logs() -> None:
    import logging
    for n in ("httpx", "httpcore"):
        logging.getLogger(n).setLevel(logging.WARNING)   # their INFO lines include full URLs, i.e. api_key=...


def _load_module(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    _quiet_http_logs()   # FastMCP(...) in these modules reconfigures logging at import
    return mod


_econ = _journal = None


def econ():
    global _econ
    if _econ is None:
        k = mcp_config.keychain_get("FRED_API_KEY")
        if k:
            os.environ["FRED_API_KEY"] = k
        _econ = _load_module("econ_gov_server", "mcp/econ_gov/server.py")
    return _econ


def journal():
    global _journal
    if _journal is None:
        _journal = _load_module("journal_server", "mcp/journal/server.py")
    return _journal


def _unwrap(env: str):
    d = json.loads(env)
    return d.get("data", d)


_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ----------------------------------------------------------------------------- status
@app.get("/api/status")
def status():
    cfg = mcp_config.build("research")["mcpServers"]
    uni = DATA / "universe_refreshed.txt"
    return {
        "mode": "research", "enabled_modes": ENABLED_MODES,
        "keys_missing": mcp_config.missing(), "servers": sorted(cfg),
        "usage": jobs.load_usage(),
        "universe_refreshed": uni.read_text().strip() if uni.exists() else None,
        "busy": bool(manager.active_llm()),
        "kronos": _kronos_status(),
    }


def _kronos_status():
    f = DATA / "cache" / "kronos" / "backtests.json"
    if not f.exists():
        return {"status": "UNVALIDATED"}
    try:
        return _load_module("kronos_track", "mcp/kronos/track.py").rank_track_record("1d", 5)   # no torch import
    except Exception as e:  # noqa: BLE001
        return {"status": "unknown", "error": str(e)[:100]}


class ModeBody(BaseModel):
    mode: str


@app.post("/api/mode")
def set_mode(body: ModeBody):
    if body.mode not in mcp_config.MODES:
        raise HTTPException(400, "unknown mode")
    if body.mode not in ENABLED_MODES:
        raise HTTPException(403, f"'{body.mode}' mode is not enabled: the per-order approval flow is not built yet.")
    return {"mode": body.mode}


# ----------------------------------------------------------------------------- macro dashboard (no LLM)
TILES = [("DFF", "Fed funds (effective)"), ("DGS2", "2-yr Treasury"), ("DGS10", "10-yr Treasury"),
         ("T10Y2Y", "10y-2y spread"), ("BAMLH0A0HYM2", "High-yield spread"), ("UNRATE", "Unemployment")]


@app.get("/api/macro")
def macro():
    def go():
        return _unwrap(econ().fed_policy_snapshot())
    try:
        return cached("macro", 1800, go)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"macro snapshot failed: {str(e)[:200]}") from e


@app.get("/api/fed/statements")
def fed_statements():
    return cached("fedlist", 6 * 3600, lambda: _unwrap(econ().fed_list_documents("statements", 4)))


# ----------------------------------------------------------------------------- watchlist (no LLM)
WATCH = DATA / "watchlist.json"


def _watch_load() -> list[str]:
    try:
        return [s for s in json.loads(WATCH.read_text()) if re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", s)]
    except (OSError, ValueError):
        return ["AAPL", "MSFT", "NVDA", "AMZN"]


class WatchBody(BaseModel):
    symbols: list[str] = Field(max_length=40)


def _close_series(df, symbol: str):
    """Close prices for one symbol from yf.download(...). The frame's shape differs between one symbol and many
    (flat vs. (ticker, field) columns, depending on the yfinance version), so try both."""
    try:
        c = df[symbol]["Close"]
    except KeyError:
        c = df["Close"]
    return c.iloc[:, 0] if hasattr(c, "columns") else c


@app.get("/api/watchlist")
def watchlist():
    symbols = _watch_load()

    def go():
        import yfinance as yf
        if not symbols:
            return []
        df = yf.download(symbols, period="1mo", interval="1d", auto_adjust=True, progress=False, group_by="ticker", threads=False)
        out = []
        for s in symbols:
            try:
                c = _close_series(df, s).dropna()
                if len(c) < 2:
                    raise ValueError
                last, prev = float(c.iloc[-1]), float(c.iloc[-2])
                lr = c.pct_change().dropna()
                out.append({"symbol": s, "last": round(last, 2), "chg_pct": round((last / prev - 1) * 100, 2),
                            "vol_ann_pct": round(float(lr.std() * (252 ** 0.5) * 100), 1),
                            "drawdown_1m_pct": round(float((c / c.cummax() - 1).min() * 100), 1),
                            "spark": [round(float(x), 2) for x in c.tail(22)]})
            except Exception:  # noqa: BLE001
                out.append({"symbol": s, "error": "no data"})
        return out
    return {"symbols": symbols, "rows": cached("watch:" + ",".join(symbols), 300, go)}


@app.put("/api/watchlist")
def watchlist_put(body: WatchBody):
    syms = []
    for s in body.symbols:
        s = s.strip().upper()
        if re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", s) and s not in syms:
            syms.append(s)
    DATA.mkdir(exist_ok=True)
    WATCH.write_text(json.dumps(syms))
    return {"symbols": syms}


# ----------------------------------------------------------------------------- journal
@app.get("/api/journal")
def journal_all():
    j = journal()
    return {"open": json.loads(j.journal_list("open")), "closed": json.loads(j.journal_list("closed", None, 50)),
            "due": json.loads(j.journal_list("due")), "calibration": json.loads(j.journal_calibration())}


class IdeaBody(BaseModel):
    symbol: str = Field(pattern=r"^[A-Za-z]{1,5}(-[A-Za-z])?$")
    direction: str
    thesis: str = Field(max_length=2000)
    entry_price: float = Field(gt=0)
    horizon_days: int = Field(ge=1, le=730)
    probability: float = Field(ge=0, le=1)
    target_price: float | None = None
    stop_price: float | None = None
    expected_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    kronos_used: bool = False
    sources: list[str] = Field(default_factory=list, max_length=40)


@app.post("/api/journal/log")
def journal_log(body: IdeaBody):
    res = json.loads(journal().journal_log_idea(**body.model_dump()))
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


class CloseBody(BaseModel):
    idea_id: int
    exit_price: float = Field(gt=0)
    outcome: str
    note: str = Field(default="", max_length=1000)


@app.post("/api/journal/close")
def journal_close(body: CloseBody):
    res = json.loads(journal().journal_close_idea(body.idea_id, body.exit_price, body.outcome, body.note))
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


# ----------------------------------------------------------------------------- reports
def _report_dirs() -> list[Path]:
    return sorted([p for p in REPORTS.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}-\d{6}", p.name)], reverse=True) if REPORTS.exists() else []


@app.get("/api/reports")
def reports():
    out = []
    for p in _report_dirs():
        task = (p / "task.txt").read_text()[:200] if (p / "task.txt").exists() else ""
        out.append({"id": p.name, "task": task, "has_report": (p / "report.md").exists()})
    return out


@app.get("/api/reports/{rid}")
def report(rid: str):
    if not re.fullmatch(r"\d{8}-\d{6}", rid):
        raise HTTPException(400, "bad id")
    p = REPORTS / rid
    if not (p / "report.md").exists():
        raise HTTPException(404, "no report")
    md = (p / "report.md").read_text()
    entries = []
    m = re.findall(r"```json\s*(\[.*?\])\s*```", md, re.S)
    if m:
        try:
            entries = json.loads(m[-1])
        except ValueError:
            entries = []
    pack = {}
    if (p / "pack.json").exists():
        try:
            pk = json.loads((p / "pack.json").read_text())
            pack = {"candidates": [c.get("symbol") for c in pk.get("candidates", [])], "gaps": pk.get("gaps", []),
                    "sources": pk.get("sources", [])}
        except ValueError:
            pass
    return {"id": rid, "task": (p / "task.txt").read_text() if (p / "task.txt").exists() else "", "markdown": md,
            "journal_entries": entries, "pack": pack}


# ----------------------------------------------------------------------------- model jobs
class AnalyzeBody(BaseModel):
    task: str = Field(min_length=10, max_length=1500)
    candidates: int = Field(default=5, ge=2, le=8)
    max_ideas: int = Field(default=3, ge=1, le=5)
    force: bool = False


def _guard(force: bool):
    if not force and (msg := jobs.usage_blocks_new_job()):
        raise HTTPException(429, msg)


def _start(kind, title, fn, llm=True):
    try:
        return manager.start(kind, title, fn, llm=llm)
    except jobs.Busy as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/analyze")
def analyze(body: AnalyzeBody):
    _guard(body.force)

    def run(job: jobs.Job):
        if jobs.FAKE:
            return _fake_pipeline(job, body.task)
        out = pipeline.run_pipeline(body.task, "research", body.candidates, body.max_ideas, emit=job.emit, cancel=job.cancel)
        return {"report_id": out.name}
    return {"job_id": _start("analyze", body.task[:80], run).id}


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    model: str = "opus"
    force: bool = False


CHAT_PROMPT_SUFFIX = """

## Conversation mode
You are chatting with the reader. Answer directly and concisely; use tools when you need current data and cite what you
used. Do not produce the full report format unless asked. Keep the same rules: no remembered prices or statistics, show
downside, give calibrated probabilities, never imply an order was placed."""


@app.post("/api/chat")
def chat(body: ChatBody):
    _guard(body.force)
    model = {"opus": cr.OPUS, "sonnet": cr.SONNET}.get(body.model)
    if not model:
        raise HTTPException(400, "model must be opus or sonnet")
    resume = body.session_id is not None
    sid = body.session_id or str(uuid.uuid4())

    def run(job: jobs.Job):
        if jobs.FAKE:
            return _fake_chat(job, body.message, sid)
        r = cr.run_claude(body.message, pipeline.analyst_prompt(3) + CHAT_PROMPT_SUFFIX, model, "research",
                          timeout_s=900, on_event=job.emit, session_id=sid, resume=resume, cancel=job.cancel)
        if r.rate_limit:
            jobs.save_usage(r.rate_limit)
        if not r.ok:
            raise RuntimeError(r.error or "chat failed")
        return {"text": r.text, "session_id": sid, "tools": len(r.tools_used)}
    return {"job_id": _start("chat", body.message[:80], run).id, "session_id": sid}


@app.post("/api/universe/refresh")
def universe_refresh():
    def run(job: jobs.Job):
        job.emit({"type": "stage", "stage": 1, "label": "Loading S&P 500 price history into Maverick (no model usage)"})
        proc = subprocess.Popen([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/refresh_universe.py")], cwd=ROOT,  # noqa: S603 - fixed argv, no user input
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if proc.stdout is None:
            raise RuntimeError("could not capture refresh output")
        for line in proc.stdout:
            if line.lstrip().startswith(("batch", "running", "screening", "503", "5")) and "INFO" not in line:
                job.emit({"type": "text", "text": line.strip()})
            if job.cancel.is_set():
                proc.kill()
                break
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"refresh exited {rc}")
        (DATA / "universe_refreshed.txt").write_text(time.strftime("%Y-%m-%d %H:%M"))
        return {"ok": True}
    return {"job_id": _start("universe", "Refresh screening universe", run, llm=False).id}


# ----------------------------------------------------------------------------- jobs / SSE
@app.get("/api/jobs/{job_id}")
def job_state(job_id: str):
    j = manager.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return {"id": j.id, "kind": j.kind, "title": j.title, "status": j.status, "result": j.result, "n_events": len(j.events)}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, start: int = 0):
    j = manager.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")

    async def gen():
        i = start
        while True:
            while i < len(j.events):
                ev = j.events[i]
                i += 1
                yield f"data: {json.dumps(ev)}\n\n"
                if ev.get("type") == "end":
                    return
            await asyncio.sleep(0.4)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    j = manager.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    j.cancel.set()
    return {"cancelling": True}


# ----------------------------------------------------------------------------- fake LLM (tests only: ECONAGENT_FAKE_LLM=1)
def _fake_pipeline(job: jobs.Job, task: str):
    out = REPORTS / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "task.txt").write_text(task)
    for stage, label, tools in ((1, "Collecting data (fake)", ["mcp__econ_gov__fed_policy_snapshot", "mcp__bls__get_multiple_series"]),
                                (2, "Writing the analysis (fake)", ["mcp__fmp__get_quote"])):
        job.emit({"type": "stage", "stage": stage, "label": label})
        for t in tools:
            time.sleep(0.6)
            job.emit({"type": "tool", "name": t})
        job.emit({"type": "stage_done", "stage": stage, "ok": True, "turns": 3, "tools": len(tools), "seconds": 1.2})
    (out / "report.md").write_text("# FAKE REPORT\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<img src=x onerror=alert(1)>\n\n"
                                   "```json\n[{\"symbol\":\"TEST\",\"direction\":\"long\",\"thesis\":\"fake\",\"entry_price\":10,"
                                   "\"target_price\":12,\"stop_price\":9,\"horizon_days\":30,\"probability\":0.5,\"sources\":[\"S1\"]}]\n```\n")
    return {"report_id": out.name}


def _fake_chat(job: jobs.Job, message: str, sid: str):
    job.emit({"type": "tool", "name": "mcp__fmp__get_quote"})
    time.sleep(0.5)
    return {"text": f"(fake reply to: {message})", "session_id": sid, "tools": 1}


# ----------------------------------------------------------------------------- static UI
@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


import live  # noqa: E402
app.include_router(live.router)
app.mount("/static", StaticFiles(directory=WEB), name="static")
