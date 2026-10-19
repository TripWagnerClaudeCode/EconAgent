---
name: kronos-forecast
description: Use Kronos (a financial candlestick foundation model) to rank stocks against peers and to sketch price/volatility paths. Use when comparing candidate tickers for relative opportunity or stress-testing a price view. Not for macro series, not a standalone buy/sell signal.
---

# Kronos forecasting skill

Kronos is a decoder-only transformer pre-trained on ~12B OHLCV bars from 45 exchanges (Shi et al., AAAI 2026).
It reads up to 512 bars of price and volume. It sees **nothing else**: no news, no fundamentals, no macro data,
no earnings dates. Tools live in the `kronos` MCP server.

## What it is good for, and the evidence

| Use | Tool | Status |
|---|---|---|
| **Relative ranking of 3-15 peers** | `kronos_rank` | The setting the paper evaluates (RankIC). **Our own out-of-sample tests disagree**: +0.20 IC on 10 mega-caps (14 origins) but -0.13 IC (t=-1.69) on 12 different names (26 origins); pooled about zero. Currently no demonstrated edge; read `track_record`. |
| Price-path sketch with uncertainty bands | `kronos_forecast` | Biased for trending names (see below). Use for shape and spread, not for the expected return number. |
| Volatility view | `kronos_volatility` | Forecast vol has run far above realised in tests. Compare across names, not to realised. |
| Re-test the signal | `kronos_rank_backtest`, `kronos_backtest` | Slow (minutes). Run after a regime change or before leaning on Kronos. |

## Known bias: do not skip this

Kronos normalises each input window by that window's own mean and standard deviation. For a stock far above or
below its window mean it predicts a snap-back. Measured on AAPL over 14 past windows (price ~ +2 std above its
window mean): predicted average 5-day return **-8.8%**, realised **+0.7%**. `kronos_forecast` reports
`window_zscore` and a `bias_warning` when |z| > 1. When you see that warning:

1. Do **not** quote the median return as a forecast.
2. Use `kronos_rank` instead; subtracting the peer median cancels the common bias.
3. State plainly in the write-up that the absolute figure is biased.

## Rules for citing Kronos in an investment view

1. Call `kronos_track_record` (or read `track_record` in any result) first. Only these statuses permit leaning on it:
   - `EARNED_TRUST`: may be cited as a supporting input.
   - `INCONCLUSIVE_MIXED`: independent runs disagree; treat the same as `NO_EDGE`.
   - `PROMISING_SMALL_SAMPLE` / `WEAK_EVIDENCE`: cite with the explicit caveat that evidence is thin.
   - `NO_EDGE` / `UNVALIDATED`: do not use it to support a view; you may mention it was run and why it was discounted.
2. Kronos is **one input**. A thesis must stand on fundamentals, macro regime, catalysts and valuation; a Kronos
   rank may adjust conviction or ordering among otherwise-qualified names, never create a thesis.
3. Quote the P10-P90 band, not just the median, and say these are the model's sampled-path spread, **not** a
   calibrated confidence interval.
4. Keep peer sets sensible: similar-sized names in comparable sectors (3-15 tickers). Ranking one stock against
   unrelated assets is meaningless.
5. Log any idea that used Kronos in the journal with `kronos_used=true`, so `journal_calibration` can later show
   whether Kronos-assisted ideas did better or worse.

## Limits to remember

- 512-bar context (about 2 years of daily bars); intraday history is capped by the data source (hourly ~2y, 15/30m ~60 days).
- Daily horizon timestamps ignore NYSE-only holidays (Good Friday), so a horizon crossing one is off by a bar.
- Backtests are frictionless (no costs/slippage) and survivorship-biased (today's tickers). They are screens, not proof.
- Model weights are pinned to the revisions upstream's regression test uses (Kronos-small + Tokenizer-base). CPU inference,
  ~1-2 s per ticker. The model unloads itself after 5 idle minutes to protect the 8 GB machine.
