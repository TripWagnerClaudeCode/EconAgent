import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "app", ROOT / "mcp" / "kronos"):
    sys.path.insert(0, str(p))


def load_module(name: str, rel: str):
    """Import one of the MCP server files by path (the `mcp/` folder is not an importable package)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
