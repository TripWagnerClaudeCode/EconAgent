"""List tools exposed by each server in the generated MCP config (no tool calls are made)."""
import asyncio, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "app"))
import mcp_config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


async def probe(name, spec):
    try:
        if spec.get("type") == "http":
            cm = streamablehttp_client(spec["url"])
            async with cm as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize(); tools = (await s.list_tools()).tools
        else:
            params = StdioServerParameters(command=spec["command"], args=spec["args"], env=spec.get("env") or None)
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize(); tools = (await s.list_tools()).tools
        return name, [t.name for t in tools], None
    except BaseException as e:  # noqa: BLE001
        return name, [], f"{type(e).__name__}: {str(e)[:200]}"


async def main(mode):
    cfg = mcp_config.build(mode)["mcpServers"]
    for name, spec in cfg.items():
        n, tools, err = await asyncio.wait_for(probe(name, spec), timeout=90)
        print(f"{n:10s} {'OK ' + str(len(tools)) + ' tools' if not err else 'FAIL ' + err}")
        if tools: print("           ", ", ".join(tools[:8]), "..." if len(tools) > 8 else "")

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "research"))
