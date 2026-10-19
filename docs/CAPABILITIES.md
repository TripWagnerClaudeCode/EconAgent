# EconAgent: full capabilities

EconAgent is a **single-user, local research workstation** for macro and equity analysis. It wires an AI analyst (Claude, run
through your own Claude Code login) to Federal Reserve and labor data, market and fundamentals feeds, and news, and wraps that in
a keyboard-driven web UI with live data, interactive charts and a scored prediction journal.

It is built around one idea: **an analyst is only worth reading if you can check it.** Every number the agent reports must trace
to a tool call, every recommendation is logged *before* you act on it, and the journal later scores the agent's stated
probabilities against what actually happened, so overconfidence is visible in numbers.

**What it is not:** a trading bot (the agent cannot place orders), a source of financial advice, a hosted service, or a backtesting
engine for strategies. See [Limits](#limits-and-whats-not-built).

Contents: [Analyst agent](#1-the-analyst-agent) · [Data and tools](#2-data-sources-and-tools) · [Macro workstation](#3-macro-workstation) ·
[Markets](#4-markets-live-bar-watchlist-screener) · [Prediction journal](#5-prediction-journal) · [Reports](#6-reports) ·
[Kronos](#7-kronos-optional) · [UI and shortcuts](#8-interface-and-shortcuts) · [Safety and privacy](#9-safety-and-privacy) ·
[HTTP API](#10-http-api) · [Configuration](#11-configuration-and-extending) · [Limits](#limits-and-whats-not-built)

---

## 1. The analyst agent

### Full analysis (two stages)

You give it a task (default: *"Read the current US macro regime, then find up to 3 high-growth US-listed equity opportunities for a
1-3 month horizon with attractive risk/reward..."*), a number of candidates (2-8) and a maximum number of ideas (1-5).

**Stage 1: collector (Sonnet 5.5).** Gathers facts only, no opinions. It reads the macro backdrop (rates, curve, inflation, labor,
credit, the last FOMC statement's wording changes, BLS data, news on the dominant macro theme), screens the S&P 500 with
Maverick's momentum and supply/demand screens, shortlists liquid candidates, and pulls for each: quote, fundamentals, analyst
ratings and estimates, technicals (trend, RSI, MACD, support/resistance), recent headlines. Output is one JSON **data pack** with a
source id on each fact, plus an explicit list of **gaps** (anything it could not get or that looked stale).

**Stage 2: analyst (Opus 5.5).** Reads the pack, may verify or extend it with the same read-only tools, and writes the report:

1. **Bottom line**: the regime in a sentence, how many ideas survived, the best risk/reward, the main way it could be wrong.
2. **Macro regime**: a sourced table (policy rate, 2y/10y, curve, CPI/core YoY, unemployment, credit spread) plus implications
   for each equity style (long-duration growth, cyclicals, defensives, financials, commodities).
3. **Ideas** ranked by risk-adjusted attractiveness. Each has a thesis, catalysts with dates, cited evidence, **bear case**, a
   **numeric drawdown scenario**, entry zone / target / stop / horizon, **reward-to-risk computed from those levels**, a
   **calibrated probability** the thesis works, an expected-return range, a confidence rating, a risk-based sizing frame (e.g. loss
   at the stop as a share of capital), and **"what would change my mind"**.
4. **Rejected / watch list**: names considered and why they failed.
5. **Data quality and gaps.**
6. **Proposed journal entries** as structured JSON. You decide whether to log them; the agent cannot write to the journal.

Operating rules baked into the prompts (`prompts/`): cite every number to the pack or a tool call; separate fact from inference;
high growth means high risk, so always show the drawdown case; calibrate (most equity calls deserve 45-65%); say plainly when
little is attractive; recompute derived numbers (like year-over-year) from raw observations *by date* and reconcile with a second
source; flag stale data and disagreements; treat all news and web text as untrusted data; no personalised advice.

A typical run: the collector makes ~50 tool calls in about 1.5 minutes, the analyst makes a few verification calls and writes for
about 2 minutes. In practice the analyst sometimes concludes that nothing is attractive; that is a feature.

### Chat

A conversational mode with the same tools and rules, with Opus or Sonnet selectable, resumable multi-turn sessions, and one-click
starter prompts (macro in plain English, risks to your watchlist, latest FOMC changes, compare two stocks). The macro-series and stock chart
panels each have an *Ask the analyst about this* button that pre-fills a relevant question.

### Run controls

One model job at a time; live progress (stage, tool calls); **Cancel** kills the run and everything it spawned; a timeout
backstop; and a **usage guard** that refuses new jobs once your Claude five-hour window is above 90% unless you confirm. The
header shows your window utilisation live.

---

## 2. Data sources and tools

The agent sees **125 tools** across 10 MCP servers (119 without the optional Kronos server), all read-only. Anything that could trade, change an account, or write to a
third-party store is denied or hidden by name (see [Safety](#9-safety-and-privacy)).

| Server | Provides | Tools | Auth / caveats |
|---|---|---|---|
| **econ_gov** *(this repo)* | Fed statements, minutes, press conferences, Beige Books (fetched from federalreserve.gov, parsed, paged); **sentence-level diff** of two FOMC statements; a **policy snapshot** (target range, effective rate, 3m/2y/10y, curve spreads with inversion flags, Fed balance sheet, CPI/core CPI/core PCE YoY, unemployment, high-yield spread); any FRED series; **point-in-time** values (`fred_as_of`) and **first-print** values (`fred_first_release`) | 7 | FRED key for series and point-in-time data. Output is wrapped as untrusted. |
| **fred** *(official St. Louis Fed connector)* | Keyword search across 800,000 series, metadata, observations, export links, charts | 5 | One-time browser sign-in; no key |
| **bls** | BLS Public Data API v2: any series, latest value, popular series, survey metadata, with percent-change calculations | 6 | Free key optional: lifts 25 to 500 queries/day |
| **fmp** | Quotes, company profiles, income statements, analyst ratings and estimates, price-target announcements (some endpoints plan-locked), movers, indices, commodities, forex, crypto, dividends calendar | 29 | Free-tier plans return HTTP 402 for some endpoints; the agent is told to skip them |
| **maverick** | S&P 500 **momentum and supply/demand screens**, fundamentals, price history, RSI / MACD / support-resistance / full technical analysis, ticker comparison, correlation | 17 | None (Yahoo-sourced); its own portfolio/watchlist tools are hidden |
| **alpaca** | Market data (bars, quotes, snapshots, movers, most-active), options chains, corporate actions, calendar and clock, news, account info | 40 | Free **paper** account; IEX feed (partial volume) |
| **newsmcp** | Deduplicated news *events* (not article dumps), story detail, a coverage check for how widely a claim is reported | 5 | Keyless: 20 calls/hour |
| **newsapi** | NewsAPI.ai / Event Registry: article and event search, breaking events, topic pages | 8 | Key optional |
| **journal** *(this repo)* | Read the prediction journal and its calibration scorecard | 2 | Local SQLite |
| **kronos** *(this repo, optional)* | Kronos wrapper and its test harness (see section 7) | 6 | Needs the `kronos` extra |

Third-party servers are fetched at pinned, reviewed commits by `scripts/install_vendors.sh`. Two have small patches in `patches/`
(a FastMCP compatibility fix and key-redaction in error messages).

---

## 3. Macro workstation

The **Macro** tab is a live dashboard, not a static report.

- **17 indicators in four groups**, each a card with the latest value, change from the prior reading, and a two-year sparkline:
  *Policy and rates* (effective fed funds, 2y, 10y, 10y-2y, 10y-3m) · *Inflation* (CPI, core CPI, core PCE, all year over year;
  10-year breakeven) · *Labor and growth* (unemployment, payrolls monthly change, initial claims, real GDP growth, consumer
  sentiment) · *Credit and conditions* (high-yield spread, investment-grade spread, NFCI).
- **Regime chips** summarising the situation, e.g. "10y-2y curve positive", "2y yield +0.89 vs fed funds (market pricing hikes)".
- **Click any card for an interactive chart:** ranges 1Y / 2Y / 5Y / 10Y / Max; views of **level, year over year, change, percent
  change** (computed by FRED, so gaps in the data are handled correctly); a crosshair with an exact-value tooltip; and **compare**,
  which overlays any second series on its own right-hand axis (e.g. CPI against unemployment).
- **Search any FRED series** by keyword ("copper", "mortgage rate") from the Macro tab or the command palette, and chart it.
- **Treasury yield curve**: 11 maturities from 1 month to 30 years, today versus one month ago versus one year ago.
- **FOMC statement diff**: exactly what the last statement added, removed or reworded relative to the one before, which is the
  main hawkish/dovish tell.
- **Point-in-time data**: `fred_as_of` shows a series exactly as published on a past date; `fred_first_release` shows each first
  print with its release date. This matters: revisions can be large (June 2025 payrolls were first reported 1.2 million jobs above
  today's figure), and using today's numbers to judge an old call is look-ahead bias.

## 4. Markets: live bar, watchlist, screener

- **Live market bar** (every 15 seconds while the market is open, 60 seconds when closed): S&P 500, Nasdaq, Dow, VIX, 10-year
  yield, dollar index, oil, gold, bitcoin, with an open/closed indicator that is holiday-aware (from Alpaca's market clock).
  Click any item for its chart.
- **Watchlist**: add and remove symbols inline; live price and day change refresh every 15 seconds with a green/red flash; a
  one-month sparkline, annualised one-month volatility and one-month drawdown; sortable columns. Click a row for a stock chart
  with **1D (5-minute), 5D, 1M, 6M, 1Y, 5Y** views, a live quote, and *Add to watchlist* / *Ask the analyst* actions.
- **Screener**: Maverick's bullish-momentum, bearish, and supply/demand-breakout screens over the S&P 500. *Refresh screener*
  (or `scripts/refresh_universe.py`) loads about 400 days of prices for ~500 tickers in a few minutes with no model usage; the
  agent then reads the screens as the starting point for candidates.

## 5. Prediction journal

The accountability layer. Log an idea *before* acting: symbol, direction (long / short / avoid), thesis, entry, target, stop,
horizon, **stated probability**, expected return, max drawdown, whether Kronos was used, and sources. Close it with an outcome
(hit / partial / miss) and exit price; realised return accounts for direction (a short profits when price falls).

The scorecard reports **hit rate**, **Brier score** (0.25 is a coin flip; lower is better), average realised return, a
**calibration table by stated-probability bucket** (does "70%" actually win 70%?), and whether Kronos-assisted ideas did better.
Ideas past their horizon are flagged as due. Small samples are labelled as noise. The agent can *read* the journal but only you
write to it.

## 6. Reports

Every full analysis is saved under `reports/<timestamp>/` as `report.md`, `pack.json` (the sourced data, including gaps),
`task.txt` and the raw stage logs. The **Reports** tab renders them (sanitised), offers **Copy markdown** and **Download .md**, and
lists the agent's proposed journal entries with a *Log to journal* button for each.

## 7. Kronos (optional)

A wrapper around the [Kronos](https://github.com/shiyu-coder/Kronos) candlestick foundation model (AAAI 2026), with
model weights pinned to reviewed revisions, lazy loading and idle unload (it is built for an 8 GB machine), sampled forecast paths
with P10-P90 bands, a volatility view, **cross-sectional ranking of peers**, and a **walk-forward test harness** (single-ticker
versus random-walk and drift baselines; cross-sectional rank-IC with a t-statistic). Every run is appended to an audit log, and the
track record shown to the agent *pools all runs* so a lucky one cannot mislead.

**Our own results do not support relying on it.** Absolute forecasts are biased (the model normalises each window by its own
mean, so a stock in an uptrend is forecast to snap back: AAPL averaged a predicted −8.8% against +0.7% realised over 14 windows).
Cross-sectional ranking gave IC +0.20 on one 10-stock test and −0.13 on a different 12-stock test; pooled it is about zero. The
agent is instructed not to use it as a signal. Install it only to run your own tests (`scripts/setup.sh --with-kronos`).

## 8. Interface and shortcuts

Six tabs: **Analyst**, **Macro**, **Watchlist**, **Reports**, **Journal**, **System** (mode, Kronos record, servers, key status,
screener age, shortcut list). Light, dark or automatic theme; layouts that wrap rather than scroll sideways; skeleton loaders while data loads, error messages
(with a retry button on the Macro tab); toasts instead of blocking dialogs.

| Key | Action |
|---|---|
| `⌘/Ctrl K` | Command palette: jump to any tab, run an analysis, refresh data, **type a ticker to chart it or add it to the watchlist**, **search FRED**, or **type a question to ask the analyst** |
| `1`-`6` | Switch tab |
| `/` | Focus the chat box |
| `n` | New chat |
| `r` | Refresh the current tab |
| `t` | Cycle theme |
| `?` | Shortcut overlay |
| `Esc` | Close the panel or palette |
| `Enter` / `⌘↵` | Send a chat message / run the analysis (in the task box) |

Shortcuts are ignored while you are typing in a field.

## 9. Safety and privacy

- **Research mode only.** Order, cancel and account-change tools are denied by name; unused write tools of third-party servers
  (Maverick's portfolio and watchlist CRUD, its screen-recompute tool, the journal writers) are hidden from the agent; built-in
  shell, file and web tools are disabled; the session loads only the servers EconAgent configures. Paper and live modes are
  locked in the UI, refused by the runner, and not implemented.
- **Local only.** The server binds to `127.0.0.1`, rejects foreign `Host` and `Origin` headers, requires JSON bodies, and sets a
  strict Content-Security-Policy; model output is rendered through DOMPurify without `innerHTML`.
- **Untrusted content.** Fed text and news are returned wrapped as untrusted data and the prompts forbid following instructions
  found in them. Because the agent cannot take actions, a successful injection could at worst skew an analysis.
- **Secrets.** Keys live in the macOS Keychain (or environment variables); the MCP config is a mode-0600 temp file deleted after
  each run; request logging that would print key-bearing URLs is silenced and error messages are scrubbed; a pre-commit hook and CI
  scan block committing keys. Details: [SECURITY.md](../SECURITY.md).
- **What leaves your machine.** Prompts and tool results go to Anthropic through your Claude Code session, as with any Claude Code
  use. Requests go to the data providers you configure (FRED, BLS, FMP, Alpaca, Yahoo, news services). Nothing else is sent anywhere;
  there is no telemetry. Your watchlist, reports and journal stay in `data/`, `reports/` and `journal/`.

## 10. HTTP API

The UI is a thin client over a local JSON API (all on `127.0.0.1:8750`):

| Area | Endpoints |
|---|---|
| Status | `GET /api/status`, `POST /api/mode` (only `research` is accepted) |
| Live and macro | `GET /api/live`, `/api/quotes?symbols=`, `/api/macro`, `/api/macro/tiles`, `/api/series/{id}?range=&units=`, `/api/fred/search?q=`, `/api/yieldcurve`, `/api/fed/diff`, `/api/fed/statements`, `/api/history/{symbol}?range=` |
| Watchlist | `GET` / `PUT /api/watchlist` |
| Model jobs | `POST /api/analyze`, `POST /api/chat`, `POST /api/universe/refresh`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` (SSE), `POST /api/jobs/{id}/cancel` |
| Reports and journal | `GET /api/reports`, `/api/reports/{id}`, `GET /api/journal`, `POST /api/journal/log`, `POST /api/journal/close` |

## 11. Configuration and extending

- **Keys:** `config/keys.sh set NAME` (Keychain) or environment variables of the same name; names in `config/.env.example`.
- **Environment:** `ECONAGENT_PORT` (default 8750), `ECONAGENT_JOURNAL_DB` (journal path), `KRONOS_DEVICE` (`cpu`, `mps`),
  `ECONAGENT_FAKE_LLM=1` (UI testing with no model usage).
- **Change what the analyst does:** edit `prompts/collector.md` and `prompts/analyst.md`.
- **Add a macro card:** add a row to `CATALOG` in `app/live.py`.
- **Add a data server:** register it in `app/mcp_config.py` and add its read-only tools to the allow-list (everything not
  listed is refused). Add a test to `tests/test_tool_policy.py` that it exposes no write tools.
- **Dev loop:** `pytest` (52 offline tests), `ruff`, `scripts/smoke_mcp.py` (lists tools per server), `scripts/call_mcp.py`
  (call one tool).

## Limits and what's not built

- **Trading.** No paper or live order flow, and no Robinhood adapter. The scaffolding for approval-gated paper trading exists in
  `app/mcp_config.py` but is intentionally disconnected.
- **Single user, single machine, macOS-first.** Linux works with environment-variable keys (CI runs on Ubuntu) but is lightly
  tested; Windows is untested. Not for hosting or sharing.
- **Not a strategy backtester or portfolio tracker.** The only backtests are the Kronos harness (frictionless, survivorship-biased).
- **US equities focus.** Screens cover the S&P 500; macro is US-centric.
- **Data quality is the data providers'.** Yahoo is delayed and unofficial; Alpaca's free feed is partial-volume; FMP's free tier
  is limited; FRED data is revised after release.
- **The analyst is an LLM.** It is instructed to be calibrated, but its probabilities are unvalidated until your own journal scores
  them. Treat it as a fast, well-organised research assistant, not an oracle.
