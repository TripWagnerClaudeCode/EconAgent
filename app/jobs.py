"""Background jobs with replayable event streams (for SSE), plus the usage-window guard."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
USAGE_FILE = ROOT / "data" / "usage.json"
USAGE_GUARD = 0.90  # refuse new LLM jobs above this five-hour utilisation unless forced


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "running"            # running | done | error | cancelled
    events: list[dict] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    llm: bool = False

    def emit(self, ev: dict) -> None:
        ev = {**ev, "t": round(time.time() - self.started, 1)}
        self.events.append(ev)
        if ev.get("type") in ("stage_done", "done") and isinstance(ev.get("rate_limit"), dict):
            save_usage(ev["rate_limit"])


class Busy(RuntimeError):
    pass


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._llm_job: str | None = None

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def active_llm(self) -> Job | None:
        j = self.jobs.get(self._llm_job or "")
        return j if j and j.status == "running" else None

    def start(self, kind: str, title: str, fn: Callable[[Job], dict], llm: bool = False) -> Job:
        with self._lock:
            if llm and self.active_llm():
                raise Busy("another model job is already running (one at a time protects your usage limits)")
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title, llm=llm)
            self.jobs[job.id] = job
            if llm:
                self._llm_job = job.id
        threading.Thread(target=self._run, args=(job, fn), daemon=True).start()
        return job

    def _run(self, job: Job, fn: Callable[[Job], dict]) -> None:
        try:
            job.result = fn(job) or {}
            job.status = "cancelled" if job.cancel.is_set() else "done"
        except Exception as e:  # noqa: BLE001
            job.status = "cancelled" if job.cancel.is_set() else "error"
            job.result = {"error": str(e)[:600]}
        job.finished = time.time()
        job.emit({"type": "end", "status": job.status, "result": job.result})


# ------------------------------------------------------------------ usage window
def save_usage(info: dict) -> None:
    """Persist the latest rate-limit snapshot Claude Code reported (five-hour / seven-day utilisation)."""
    try:
        wins = info.get("unifiedWindows") or {}
        snap = {"five_hour": wins.get("five_hour"), "seven_day": wins.get("seven_day"),
                "status": info.get("status"), "saved_at": time.time()}
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps(snap))
    except Exception:  # noqa: BLE001
        pass


def load_usage() -> dict:
    try:
        u = json.loads(USAGE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    fh = u.get("five_hour") or {}
    if fh.get("resetsAt") and fh["resetsAt"] < time.time():   # window already rolled over: stale reading
        u["five_hour"] = {**fh, "utilization": 0.0, "stale": True}
    return u


def usage_blocks_new_job() -> str | None:
    fh = (load_usage().get("five_hour") or {})
    if (fh.get("utilization") or 0) >= USAGE_GUARD:
        reset = time.strftime("%I:%M %p", time.localtime(fh.get("resetsAt", 0)))
        return f"Your five-hour Claude usage window is {fh['utilization']:.0%} used (resets {reset}). Wait, or run anyway."
    return None


FAKE = bool(os.environ.get("ECONAGENT_FAKE_LLM"))
