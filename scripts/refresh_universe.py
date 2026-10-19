"""Load a stock universe into Maverick and run its screens (no LLM involved).

Maverick's screens only see tickers whose price history has been loaded. This pulls the S&P 500 constituent list,
loads ~1y of daily bars in batches, then recomputes all three screens. Re-run it daily/weekly; takes a few minutes.

usage: refresh_universe.py [--limit N] [--batch 25]
"""
import argparse, asyncio, json, re, sys, time
from datetime import date, timedelta
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import mcp_config  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=0, help="only the first N tickers (for testing)")
ap.add_argument("--batch", type=int, default=25)
args = ap.parse_args()


def sp500() -> list[str]:
    r = httpx.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                  headers={"User-Agent": "EconAgent/0.1 (personal research)"}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    table = BeautifulSoup(r.text, "html.parser").find("table", id="constituents")
    out = []
    for row in table.find_all("tr")[1:]:
        cell = row.find("td")
        sym = cell.get_text(strip=True).replace(".", "-") if cell else ""
        if re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", sym):  # validate: scraped text never goes into a tool call unchecked
            out.append(sym)
    return out


async def main():
    tickers = sp500()
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"{len(tickers)} tickers", flush=True)
    spec = mcp_config.build("research")["mcpServers"]["maverick"]
    params = StdioServerParameters(command=spec["command"], args=spec["args"], env=spec.get("env") or None)
    start = (date.today() - timedelta(days=400)).isoformat()
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for i in range(0, len(tickers), args.batch):
                chunk = tickers[i: i + args.batch]
                t = time.time()
                res = await s.call_tool("market_data_get_price_history_batch", {"tickers": chunk, "start_date": start})
                txt = "".join(getattr(c, "text", "") for c in res.content)
                try:
                    d = json.loads(txt)
                    ok = sum(1 for v in (d.get("results") or d.get("data") or {}).values()) if isinstance(d, dict) else "?"
                except ValueError:
                    ok = "?"
                print(f"  batch {i // args.batch + 1}: {len(chunk)} tickers, {ok} returned, {time.time() - t:.0f}s", flush=True)
                await asyncio.sleep(2)
            print("running screens ...", flush=True)
            res = await s.call_tool("screening_run_screens", {})
            print("".join(getattr(c, "text", "") for c in res.content)[:400])
            for tool in ("screening_get_bullish", "screening_get_supply_demand"):
                res = await s.call_tool(tool, {})
                txt = "".join(getattr(c, "text", "") for c in res.content)
                try:
                    print(tool, "->", json.loads(txt).get("count"), "results")
                except ValueError:
                    print(tool, txt[:200])

asyncio.run(main())
