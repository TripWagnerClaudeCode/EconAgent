# EconAgent

**A local research workstation that pairs live macro and market data with an LLM analyst.** EconAgent connects an AI agent (Claude, running through *your own* Claude Code login) to Federal Reserve and labor data, market and fundamentals feeds, and news, then has it act as a senior financial analyst: read the macro regime, screen for high-growth opportunities, and weigh each idea's potential profit against its downside. Every number is cited to a tool call, every idea is logged to a scored prediction journal, and the agent has no ability to place trades.

> **Not financial advice.** EconAgent is a research tool. It can be wrong, its data can be stale or delayed, and past analysis is not a prediction. You are responsible for any decision you make.

## What it does

This is the short version. **[docs/CAPABILITIES.md](docs/CAPABILITIES.md) describes every capability in detail**: the agent's report structure and rules, all 125 tools by source, the macro and market features, the journal's scoring, keyboard shortcuts, the HTTP API, and exactly what is *not* built.

| | |
|---|---|
| **Analyst agent** | A two-stage pipeline: a fast model (Sonnet) gathers a sourced data pack from ~125 read-only tools, then a stronger model (Opus) writes the report: macro regime, ranked ideas, bear case, drawdown scenario, entry/target/stop, reward-to-risk, a calibrated probability, and "what would change my mind". Plus a chat mode with the same tools. |
| **Macro** | Interactive dashboard of 17 indicators (rates, curve, inflation, labor, growth, credit) with sparklines; click any card for a chart with ranges, level / YoY / change views, and a second series overlaid on its own axis. Search all 800,000 FRED series, an interactive Treasury yield curve, and a sentence-level diff of the last two FOMC statements. Point-in-time (ALFRED) tools let you see data *as it was published*, so analyses and backtests don't use revised numbers. |
| **Live data** | Market bar (S&P 500, Nasdaq, Dow, VIX, 10-year, dollar, oil, gold, bitcoin, market open/closed) and a self-refreshing watchlist with intraday charts, volatility and drawdown. |
| **Screening** | A momentum / supply-demand screen over the S&P 500, plus fundamentals, analyst ratings, technicals and news for each candidate. |
| **Prediction journal** | Log the agent's calls *before* acting on them, close them with the outcome, and track hit rate and Brier score by confidence bucket, so overconfidence shows up in numbers. |
| **Keyboard-driven UI** | `⌘/Ctrl K` palette (jump anywhere, chart a ticker, search FRED, ask a question), `1`–`6` tabs, `/` to ask, `r` refresh, `t` theme, `?` help. Light/dark. |
| **Optional: Kronos** | Wrapper for the [Kronos](https://github.com/shiyu-coder/Kronos) candlestick foundation model, with a built-in walk-forward test harness. See "Honest limitations". |

## How it works

```
Browser UI (127.0.0.1 only) ── HTTP / SSE ──> FastAPI backend (app/)
                                                   │ spawns your logged-in `claude -p`
                                                   ▼
        isolated session: --strict-mcp-config, no built-in tools, explicit allow / deny lists
                                                   │
   ┌──────────────┬───────────────┬──────────────┼──────────────┬──────────────┐
 FRED (official)  BLS            Alpaca (data)   FMP            Maverick     NewsMCP / NewsAPI.ai
 econ_gov (Fed text, FRED/ALFRED)   journal        kronos (optional)
```

The agent runs in a locked-down Claude Code session: only the MCP servers EconAgent configures, no shell or file tools, and an explicit allow-list of read-only tools. Trading and account-changing tools are *denied by name* (so they are not just discouraged by the prompt, they do not exist for the agent). Custom MCP servers live in `mcp/`: `econ_gov` (Federal Reserve statements, minutes and Beige Book, FRED snapshot and point-in-time data), `journal`, and `kronos`. Third-party servers are fetched at pinned commits (`scripts/vendors.txt`).

## Requirements

- **macOS** (primary target; keys live in the Keychain). Linux should work with keys supplied as environment variables, but is untested.
- [**Claude Code**](https://docs.claude.com/claude-code), logged in. EconAgent drives the unmodified `claude` binary using your own subscription. Tested with Claude Code 2.1.x and the Claude 5.5 model family; a Pro plan works, but a full analysis uses a meaningful share of a five-hour window (the app shows your usage and warns you at 90%).
- [`uv`](https://docs.astral.sh/uv/), `git`, and Node.js 20+ (`brew install uv node`).
- Free API keys, below. Only FRED is strictly needed for the macro tab; the analyst agent works best with all of them.

## Quick start

```bash
git clone https://github.com/TripWagnerClaudeCode/EconAgent.git
cd EconAgent
scripts/setup.sh                      # Python env + pinned third-party servers (add --with-kronos for the optional model)

config/keys.sh set FRED_API_KEY       # prompts with hidden input; stored in the macOS Keychain
# ...repeat for the other keys below (names only in config/.env.example)

claude                                # once, to log in (then /exit)
.venv/bin/python -m pytest            # 52 offline tests
.venv/bin/python scripts/serve.py     # http://127.0.0.1:8750
```

In the app: open **Analyst**, click **Refresh screener** once (loads S&P 500 prices; no model usage), then **Run analysis**.

### Data sources and keys

| Source | Key | Notes |
|---|---|---|
| FRED (St. Louis Fed) | `FRED_API_KEY`, free | [fredaccount.stlouisfed.org/apikeys](https://fredaccount.stlouisfed.org/apikeys). Powers macro tiles, charts, search, yield curve, point-in-time data. |
| BLS | `BLS_API_KEY`, free, optional | Raises the daily limit from 25 to 500 queries. |
| Alpaca | `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | A free **paper** account is enough. Used read-only, market data on the IEX feed (partial volume). |
| FMP | `FMP_API_KEY` | Free tier works for quotes, statements, ratings and movers; some endpoints return 402 on free plans and the agent is told to skip them. |
| NewsAPI.ai | `NEWSAPI_KEY`, optional | NewsMCP (keyless, 20 calls/hour) already covers news. |
| Official FRED MCP connector | none | Optional: run `claude` in the project folder, `/mcp`, authenticate `fred`. |
| Yahoo Finance (via `yfinance`) | none | Live bar, watchlist, history. Unofficial and delayed; for personal use. |

Keys come from the Keychain first, then from environment variables of the same name.

## Using it

- **Analyst tab**: *Run analysis* for the full report, or chat. Reports are saved to `reports/` and appear in the **Reports** tab, with the agent's proposed journal entries. Nothing is logged until you click *Log to journal*.
- **Journal**: close each idea with its outcome; the scorecard reports hit rate and Brier score (0.25 = a coin flip).
- **System**: key status, server list, screener age, Kronos record, shortcuts.

## Safety model

- **Research mode only.** Order, cancel and account-change tools are denied by name; paper and live modes are locked in the UI, refused by the runner, and not implemented (the scaffolding in `app/mcp_config.py` is deliberately not wired up).
- **Local only.** The server binds to `127.0.0.1`, rejects any other `Host` (DNS rebinding) or cross-site `Origin` (CSRF), requires JSON bodies, and sets a strict Content-Security-Policy. Model output is rendered through DOMPurify; there is no `innerHTML` in the UI.
- **Untrusted content.** News, Fed text and web data reach the model wrapped as untrusted data, and the prompts instruct it never to follow instructions found inside them.
- **Secrets.** Keys are never written to files or placed on command lines; request logging that would print API-key URLs is silenced, and tool error messages are scrubbed of keys. See [SECURITY.md](SECURITY.md).
- **Pinned supply chain.** Third-party MCP servers receive your keys, so they are cloned at exact reviewed commits.

## Honest limitations

- **Kronos shows no demonstrated edge in our own tests.** Its absolute forecasts are biased toward mean-reverting its own input window (AAPL: predicted −8.8% vs +0.7% realised over 14 windows). Cross-sectional ranking, the setting its paper evaluates, gave IC +0.20 on one 10-stock test and −0.13 on a different 12-stock test; pooled it is about zero. It is therefore optional context and the agent is told never to use it as a signal. Re-run the harness on your own universe before trusting it.
- **LLM analysis is not a model of the market.** The agent is instructed to be calibrated and to say when little is attractive, but its probabilities are unvalidated until your own journal scores them.
- **Data is imperfect**: Yahoo is delayed and unofficial, Alpaca's free feed is partial-volume, FMP's free tier is limited, and FRED series can be revised (use the point-in-time tools for backtests).
- **Backtests here are frictionless** (no costs or slippage) and survivorship-biased.
- Single-user, single-machine, macOS-first.

## Terms of use for Claude

EconAgent shells out to the unmodified Claude Code CLI signed in with **your own** account, for **personal** use. Anthropic's [terms](https://code.claude.com/docs/en/legal-and-compliance) say products built on the Agent SDK should use API keys, and that subscription login must not be offered to other users or routed on their behalf. Do not host this for others or share your login. If you want to build on this for anyone but yourself, switch to API-key authentication.

## Development

```bash
scripts/install_hooks.sh                   # pre-commit hook: refuses any commit containing one of your API keys
.venv/bin/python -m pytest                 # offline tests: tool policy, data bugs, journal math, HTTP guards
uvx ruff check app mcp scripts tests       # lint
.venv/bin/python scripts/smoke_mcp.py      # lists tools for every configured MCP server
```

Layout: `app/` backend and runner · `mcp/` custom MCP servers · `prompts/` collector and analyst prompts · `skills/` Kronos skill · `web/` UI (vanilla JS, no build step) · `scripts/` setup, serving, universe refresh · `patches/` fixes applied to third-party servers · `tests/`.

## License

MIT, see [LICENSE](LICENSE). Third-party servers fetched into `vendor/` are each MIT-licensed by their authors.
