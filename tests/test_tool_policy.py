"""The safety contract: in research mode the agent can read everything and place no orders."""
import pytest

import claude_runner
import mcp_config as m


def test_research_allows_no_order_or_write_tools():
    p = m.tool_policy("research")
    allowed = " ".join(p["allowed"])
    for bad in ("place_", "cancel_", "replace_order", "close_position", "exercise", "update_account_config",
                "portfolio_add_position", "portfolio_clear_portfolio", "journal_log_idea", "journal_close_idea"):
        assert bad not in allowed, bad


def test_research_explicitly_denies_trading_and_writes():
    d = set(m.tool_policy("research")["disallowed"])
    assert "mcp__alpaca__update_account_config" in d          # ships inside the read-only `account` toolset
    for t in m.ALPACA_TRADING_TOOLS:
        assert f"mcp__alpaca__{t}" in d
    for t in m.MAVERICK_HIDDEN:
        assert f"mcp__maverick__{t}" in d
    for t in m.JOURNAL_WRITE:
        assert f"mcp__journal__{t}" in d


def test_journal_write_can_be_enabled_explicitly():
    p = m.tool_policy("research", journal_write=True)
    assert "mcp__journal__journal_log_idea" in p["allowed"]
    assert "mcp__journal__journal_log_idea" not in p["disallowed"]


def test_maverick_lists_do_not_overlap():
    assert not set(m.MAVERICK_READ) & set(m.MAVERICK_HIDDEN)


def test_alpaca_research_toolsets_exclude_trading(monkeypatch):
    monkeypatch.setattr(m, "keychain_get", lambda n: "x")
    env = m.build("research")["mcpServers"]["alpaca"]["env"]
    toolsets = env["ALPACA_TOOLSETS"].split(",")
    assert "trading" not in toolsets and env["ALPACA_PAPER_TRADE"] == "true"


def test_order_approval_flow_not_implemented_so_other_modes_refuse():
    with pytest.raises(ValueError):
        claude_runner.run_claude("hi", "sys", claude_runner.SONNET, mode="paper")
    with pytest.raises(ValueError):
        claude_runner.run_claude("hi", "sys", claude_runner.SONNET, mode="live")


def test_unknown_mode_rejected():
    with pytest.raises(ValueError):
        m.tool_policy("yolo")
    with pytest.raises(ValueError):
        m.build("yolo")


def test_build_without_keys_omits_keyed_servers_and_leaks_nothing(monkeypatch):
    monkeypatch.setattr(m, "keychain_get", lambda n: None)
    cfg = m.build("research")["mcpServers"]
    assert {"alpaca", "fmp", "newsapi"}.isdisjoint(cfg)
    assert {"fred", "bls", "maverick", "newsmcp"} <= set(cfg)
    assert m.missing()  # reports what is absent


def test_key_fallback_to_environment_on_non_macos(monkeypatch):
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setenv("FMP_API_KEY", "from-env")
    assert m.keychain_get("FMP_API_KEY") == "from-env"
    monkeypatch.delenv("FMP_API_KEY")
    assert m.keychain_get("FMP_API_KEY") is None


def test_every_server_env_pins_log_level(monkeypatch):
    monkeypatch.setattr(m, "keychain_get", lambda n: "x")
    for name, spec in m.build("research")["mcpServers"].items():
        if "env" in spec and name in ("fmp", "econ_gov", "journal", "bls", "newsapi", "alpaca"):
            assert spec["env"].get("FASTMCP_LOG_LEVEL") == "WARNING", name
