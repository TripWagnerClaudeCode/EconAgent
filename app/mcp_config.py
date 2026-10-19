"""Builds the per-mode MCP config handed to `claude -p --mcp-config <json> --strict-mcp-config`.

Secrets come from the macOS Keychain (service "econagent") at call time. The runner writes the config to a mode-0600
temp file that is deleted when the run ends (never inline on the command line, where `ps` would show the keys).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
MCP_DIR = ROOT / "mcp"
KEY_SERVICE = "econagent"

MODES = ("research", "paper", "live")

# Alpaca toolsets. `trading` (orders/positions) is the only one that can move money;
# `watchlists` writes but is harmless. Research mode never loads `trading`.
ALPACA_READ_TOOLSETS = [
    "account", "assets", "stock-data", "crypto-data", "options-data",
    "corporate-actions", "news", "fixed-income-data",
]
ALPACA_TRADING_TOOLSETS = ALPACA_READ_TOOLSETS + ["trading", "watchlists"]


_KC_TTL = 30.0
_kc_cache: dict[str, tuple[float, str | None]] = {}


def keychain_get(name: str) -> str | None:
    """Look a key up in the macOS Keychain (service "econagent"), then fall back to an environment variable of the
    same name (FRED_API_KEY, ...). The fallback is how Linux/Docker/CI supply keys; on macOS prefer the Keychain."""
    if sys.platform == "darwin":
        hit = _kc_cache.get(name)   # status polling would otherwise spawn dozens of `security` processes a minute
        if hit and time.time() - hit[0] < _KC_TTL:
            if hit[1]:
                return hit[1]
        else:
            val = _keychain_lookup(name)
            _kc_cache[name] = (time.time(), val)
            if val:
                return val
    return os.environ.get(name) or None


def _keychain_lookup(name: str) -> str | None:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv; `security` is the macOS Keychain CLI
            ["/usr/bin/security", "find-generic-password", "-s", KEY_SERVICE, "-a", name, "-w"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _env(*names: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {n: v for n in names if (v := keychain_get(n))}
    env.update(extra or {})
    # FastMCP-based servers log every outbound request URL at INFO, and several APIs (FMP, FRED) carry the key in the
    # URL. WARNING keeps keys out of stderr and anything that captures it.
    env.setdefault("FASTMCP_LOG_LEVEL", "WARNING")
    return env


def build(mode: str = "research") -> dict:
    """Return the {"mcpServers": {...}} dict for a trading mode.

    Servers whose required key is missing are omitted (and reported by `missing()`),
    so a partially configured install still runs.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    servers: dict[str, dict] = {}

    # Official St. Louis Fed connector. Remote, OAuth sign-in (one-time, interactive via `/mcp`), no API key.
    servers["fred"] = {"type": "http", "url": "https://mcp.stlouisfed.org"}

    # BLS Public Data API v2 (larasrinath/bls_mcp). Works keyless at 25 queries/day; a free key lifts it to 500.
    servers["bls"] = {
        "command": "node",
        "args": [str(VENDOR / "bls_mcp" / "dist" / "index.js")],
        "env": _env("BLS_API_KEY"),
    }

    if keychain_get("ALPACA_API_KEY") and keychain_get("ALPACA_SECRET_KEY"):
        trading = mode in ("paper", "live")
        servers["alpaca"] = {
            "command": "uvx",
            "args": ["alpaca-mcp-server==2.4.0"],
            "env": _env(
                "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                extra={
                    "ALPACA_PAPER_TRADE": "false" if mode == "live" else "true",
                    "ALPACA_TOOLSETS": ",".join(ALPACA_TRADING_TOOLSETS if trading else ALPACA_READ_TOOLSETS),
                },
            ),
        }

    # cdtait/fmp-mcp-server over stdio (vendored, with a FastMCP compat patch: vendor/fmp-fastmcp-compat.patch).
    # Without a key the server silently falls back to FMP "demo" mode, so only register it when a key exists.
    if keychain_get("FMP_API_KEY"):
        fmp_dir = VENDOR / "fmp-mcp-server"
        servers["fmp"] = {
            "command": str(fmp_dir / ".venv" / "bin" / "python"),
            "args": ["-m", "src.server"],
            "env": _env("FMP_API_KEY", extra={"PYTHONPATH": str(fmp_dir)}),
        }

    servers["maverick"] = {
        "command": "uv",
        "args": ["run", "--project", str(VENDOR / "maverick-mcp"), "maverick-mcp", "--transport", "stdio"],
        # Tiingo unavailable; Maverick falls back to yfinance. DBs pinned under data/ (default is the cwd).
        "env": {"DATABASE_URL": f"sqlite:///{ROOT / 'data' / 'maverick.db'}",
                "CACHE_SQLITE_PATH": str(ROOT / "data" / "maverick_cache.db")},
    }

    servers["newsmcp"] = {"type": "http", "url": "https://mcp.newsmcp.com/mcp"}

    if keychain_get("NEWSAPI_KEY"):
        servers["newsapi"] = {
            "command": "node",
            "args": [str(VENDOR / "newsapi-mcp" / "dist" / "index.js")],
            "env": _env("NEWSAPI_KEY"),
        }

    # Custom servers (built in later phases); included once their entry point exists.
    uv_python = str(ROOT / ".venv" / "bin" / "python")
    for name, rel, keys in (
        ("econ_gov", "econ_gov/server.py", ("FRED_API_KEY",)),
        ("kronos", "kronos/server.py", ()),
        ("journal", "journal/server.py", ()),
    ):
        entry = MCP_DIR / rel
        if name == "kronos" and not kronos_ready():
            continue   # optional extra: needs vendor/Kronos and torch
        if entry.exists():
            servers[name] = {"command": uv_python, "args": [str(entry)], "env": _env(*keys)}

    return {"mcpServers": servers}


def kronos_ready() -> bool:
    """Kronos is an optional extra: only register it when the vendored model code and torch are both present."""
    return (VENDOR / "Kronos" / "model").is_dir() and importlib.util.find_spec("torch") is not None


def missing() -> list[str]:
    """Key names that are not yet in the Keychain."""
    names = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "FMP_API_KEY", "NEWSAPI_KEY",
             "BLS_API_KEY", "FRED_API_KEY"]  # last two optional (higher limits / ALFRED vintages)
    return [n for n in names if not keychain_get(n)]


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "research"
    cfg = build(mode)
    # Redact values when printing for humans.
    redacted = json.loads(json.dumps(cfg))
    for s in redacted["mcpServers"].values():
        for k in s.get("env", {}):
            s["env"][k] = "***"
    print(json.dumps(redacted, indent=2))
    print("missing keys:", ", ".join(missing()) or "none", file=sys.stderr)


# --------------------------------------------------------------------------- tool permissions per mode
# Enforced by Claude Code itself (--allowedTools / --disallowedTools), not by the prompt.
READ_SERVERS = ["fred", "bls", "fmp", "newsmcp", "newsapi", "econ_gov", "kronos"]

# Maverick: allow its read/analysis tools only. Its portfolio_*/watchlist_*/journal_* tools write to a local SQLite
# portfolio and include portfolio_clear_portfolio; EconAgent keeps its own journal, so none of those are exposed.
MAVERICK_READ = [
    "market_data_get_price_history", "market_data_get_price_history_batch", "market_data_get_quote",
    "market_data_get_stock_fundamentals", "market_data_get_market_overview", "market_data_get_chart_links",
    "screening_get_bullish", "screening_get_bearish", "screening_get_supply_demand", "screening_get_all",
    "screening_get_by_criteria", "portfolio_compare_tickers",
    "portfolio_correlation_analysis", "technical_get_rsi_analysis", "technical_get_macd_analysis",
    "technical_get_support_resistance", "technical_get_full_technical_analysis",
]
JOURNAL_READ = ["journal_list", "journal_calibration"]

# Alpaca tools that change account state or place orders. `update_account_config` ships inside the read-only
# `account` toolset, so it must be denied by name even in research mode.
ALPACA_TRADING_TOOLS = ["place_stock_order", "place_crypto_order", "place_option_order", "cancel_all_orders",
                        "cancel_order_by_id", "replace_order_by_id", "close_position", "close_all_positions",
                        "exercise_options_position", "do_not_exercise_options_position"]


def _mcp(server: str, tool: str | None = None) -> str:
    return f"mcp__{server}" if tool is None else f"mcp__{server}__{tool}"


# Every other Maverick tool (its own local portfolio / watchlist / trade-journal CRUD, cache clearing). Denied by name so
# they disappear from the model's tool list entirely (allow rules only auto-approve; deny rules also hide).
MAVERICK_HIDDEN = [
    "market_data_clear_market_cache", "portfolio_get_my_portfolio", "portfolio_risk_adjusted_analysis",
    "portfolio_get_risk_dashboard", "portfolio_check_position_risk", "portfolio_get_regime_adjusted_sizing",
    "portfolio_get_risk_alerts", "portfolio_watchlist_brief", "portfolio_watchlist_list",
    "portfolio_journal_list_trades", "portfolio_journal_review", "portfolio_get_strategy_performance",
    "portfolio_add_position", "portfolio_remove_position", "portfolio_clear_portfolio",
    "portfolio_watchlist_create", "portfolio_watchlist_add", "portfolio_watchlist_remove",
    "portfolio_journal_add_trade", "portfolio_journal_close_trade",
    "screening_run_screens",   # recomputes + persists screen results in Maverick's local DB; scripts/refresh_universe.py does this, not the agent
]
JOURNAL_WRITE = ["journal_log_idea", "journal_close_idea"]
# Alpaca's docs-lookup tools pull remote text (untrusted) and add nothing to analysis; hide them.
ALPACA_DOCS_TOOLS = ["search_alpaca_docs", "fetch_alpaca_doc", "search_alpaca_api_specs",
                     "list_alpaca_api_endpoints", "get_alpaca_endpoint_docs"]

ALPACA_READ_TOOLS = [
    "get_account_info", "get_account_activities", "get_portfolio_history", "get_all_assets", "get_asset",
    "get_calendar", "get_clock", "get_news", "get_market_movers", "get_most_active_stocks", "get_stock_bars",
    "get_stock_snapshot", "get_stock_latest_quote", "get_stock_latest_bar", "get_stock_quotes", "get_stock_trades",
    "get_option_chain", "get_option_snapshot", "get_corporate_action_announcements", "get_corporate_actions",
]
ALPACA_PAPER_EXTRA_READ = ["get_orders", "get_all_positions", "get_open_position"]


def tool_policy(mode: str = "research", journal_write: bool = False) -> dict[str, list[str]]:
    """{'allowed': [...], 'disallowed': [...]} for `claude -p` (pair with --tools "" to drop built-in tools).

    Alpaca is allowed by explicit read-tool names only. In paper/live mode the order tools are loaded by the server
    but deliberately left OUT of `allowed`: the backend must route each order through a human approval card.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}")
    allowed = [_mcp(s) for s in READ_SERVERS]
    allowed += [_mcp("maverick", t) for t in MAVERICK_READ]
    allowed += [_mcp("journal", t) for t in JOURNAL_READ + (JOURNAL_WRITE if journal_write else [])]
    allowed += [_mcp("alpaca", t) for t in ALPACA_READ_TOOLS + (ALPACA_PAPER_EXTRA_READ if mode != "research" else [])]
    disallowed = [_mcp("alpaca", "update_account_config")] + [_mcp("maverick", t) for t in MAVERICK_HIDDEN]
    disallowed += [_mcp("alpaca", t) for t in ALPACA_DOCS_TOOLS]
    if not journal_write:
        disallowed += [_mcp("journal", t) for t in JOURNAL_WRITE]
    if mode == "research":
        disallowed += [_mcp("alpaca", t) for t in ALPACA_TRADING_TOOLS]
    return {"allowed": allowed, "disallowed": disallowed}
