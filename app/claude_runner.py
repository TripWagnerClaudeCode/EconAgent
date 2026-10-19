"""Run one headless Claude Code turn on the user's own logged-in subscription (no API key, no token handling).

This shells out to the unmodified `claude` binary, the same pattern as the Dash app. It never reads, stores or
forwards OAuth credentials, and it unsets ANTHROPIC_API_KEY so the subscription login is what gets used.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import mcp_config

ROOT = Path(__file__).resolve().parent.parent
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"


@dataclass
class RunResult:
    text: str = ""
    ok: bool = False
    error: str | None = None
    tools_used: list[str] = field(default_factory=list)
    num_turns: int | None = None
    duration_s: float = 0.0
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    rate_limit: dict | None = None
    log_path: Path | None = None


def run_claude(prompt: str, system_prompt: str, model: str, mode: str = "research", journal_write: bool = False,
               log_path: Path | None = None, timeout_s: int = 1500,
               on_event: Callable[[dict], None] | None = None,
               session_id: str | None = None, resume: bool = False,
               cancel: Any = None) -> RunResult:
    """Run one headless turn. on_event receives {"type": "tool"|"text", ...}.

    session_id + resume=False starts a persisted session with that id; resume=True continues it (multi-turn chat).
    Without session_id nothing is persisted. `cancel` is a threading.Event; setting it kills the run.
    """
    if mode != "research":
        # paper/live scaffolding exists in mcp_config, but order approval is not implemented: refuse rather than half-work.
        raise ValueError("only research mode is implemented; trading modes are disabled")
    cfg = mcp_config.build(mode)
    policy = mcp_config.tool_policy(mode, journal_write=journal_write)
    # Only reference servers that actually exist in this config (a missing key drops its server).
    live = set(cfg["mcpServers"])
    allowed = [t for t in policy["allowed"] if t.split("__")[1] in live]

    fd, cfg_path = tempfile.mkstemp(prefix="econagent-mcp-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh)
        cmd = [
            "claude", "-p", "--model", model,
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", cfg_path, "--strict-mcp-config",
            "--tools", "",                      # no built-in tools (no Bash/Edit/Write/Web)
            "--setting-sources", "project",     # skip user plugins/skills/hooks; cwd has no project settings
            "--permission-mode", "dontAsk",     # anything not in --allowedTools is refused, never prompted
            "--allowedTools", ",".join(allowed),
            "--disallowedTools", ",".join(policy["disallowed"]),
            "--append-system-prompt", system_prompt,
        ]
        if session_id:
            cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
        else:
            cmd += ["--no-session-persistence"]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        res = RunResult(log_path=log_path)
        t0 = time.time()
        logf = open(log_path, "w") if log_path else None
        try:
            errf = tempfile.TemporaryFile(mode="w+")   # stderr to a file: an undrained pipe could fill and deadlock the run
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # noqa: S603 - argv built from our own config
                                    stderr=errf, text=True, start_new_session=True)   # own process group, so we can kill its children too
            if proc.stdin is None or proc.stdout is None:
                raise RuntimeError("could not open pipes to claude")
            proc.stdin.write(prompt)
            proc.stdin.close()
            deadline = t0 + timeout_s
            finished = threading.Event()

            def kill_group() -> None:
                # claude spawns the MCP servers; killing only the parent would leave them running and holding our pipe
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()

            def watchdog() -> None:
                # Enforces the timeout and Cancel even when claude is silent (a plain read loop only notices on output).
                while not finished.wait(0.5):
                    if time.time() > deadline:
                        res.error = f"timed out after {timeout_s}s"
                    elif cancel is not None and cancel.is_set():
                        res.error = "cancelled"
                    else:
                        continue
                    kill_group()
                    return

            threading.Thread(target=watchdog, daemon=True).start()
            for line in proc.stdout:
                if logf:
                    logf.write(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind = ev.get("type")
                if kind == "assistant":
                    for block in ev.get("message", {}).get("content", []):
                        if block.get("type") == "tool_use":
                            res.tools_used.append(block.get("name", "?"))
                            if on_event:
                                on_event({"type": "tool", "name": block.get("name", "?")})
                        elif block.get("type") == "text" and block.get("text", "").strip() and on_event:
                            on_event({"type": "text", "text": block["text"]})
                elif kind == "rate_limit_event":
                    res.rate_limit = ev.get("rate_limit_info") or ev
                elif kind == "result":
                    res.text = ev.get("result") or ""
                    res.ok = ev.get("subtype") == "success" and not ev.get("is_error")
                    res.num_turns = ev.get("num_turns")
                    res.usage = ev.get("usage") or {}
                    res.cost_usd = ev.get("total_cost_usd")
                    if not res.ok:
                        res.error = f"{ev.get('subtype')}: {res.text[:300]}"
            proc.wait(timeout=30)
            if not res.ok and not res.error:
                errf.seek(0)
                err = errf.read()[-500:]
                res.error = f"claude exited {proc.returncode}: {err}"
        finally:
            finished.set()
            if logf:
                logf.close()
            errf.close()
        res.duration_s = round(time.time() - t0, 1)
        return res
    finally:
        try:
            os.unlink(cfg_path)
        except OSError:
            pass
