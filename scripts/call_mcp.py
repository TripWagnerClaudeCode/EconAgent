"""Call one tool on one configured server: call_mcp.py <server> <tool> '<json args>' [mode]"""
import asyncio, json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app"))
import mcp_config
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main(server, tool, args, mode):
    spec = mcp_config.build(mode)["mcpServers"][server]
    params = StdioServerParameters(command=spec["command"], args=spec["args"], env=spec.get("env") or None)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            for c in res.content:
                print(getattr(c, "text", str(c)))
            if res.isError: print("[tool reported error]")

asyncio.run(asyncio.wait_for(main(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else "research"), 120))
