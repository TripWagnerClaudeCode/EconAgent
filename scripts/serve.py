"""Start the EconAgent UI on 127.0.0.1 only: .venv/bin/python scripts/serve.py  ->  http://127.0.0.1:8750"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
os.chdir(ROOT)
import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ.get("ECONAGENT_PORT", "8750")), log_level="warning")
