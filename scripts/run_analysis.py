"""CLI wrapper for the two-stage pipeline. usage: run_analysis.py "<task>" [--candidates 6] [--max-ideas 3]"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import pipeline  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("task")
ap.add_argument("--mode", default="research", choices=["research"])
ap.add_argument("--max-ideas", type=int, default=3)
ap.add_argument("--candidates", type=int, default=6)
a = ap.parse_args()
try:
    out = pipeline.run_pipeline(a.task, a.mode, a.candidates, a.max_ideas, emit=lambda e: print(e, flush=True))
except pipeline.PipelineError as e:
    sys.exit(str(e))
print(f"\nreport: {out / 'report.md'}")
