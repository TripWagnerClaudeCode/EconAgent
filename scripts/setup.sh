#!/usr/bin/env bash
# One-shot setup:  scripts/setup.sh [--with-kronos]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v claude >/dev/null || echo "NOTE: the 'claude' CLI (Claude Code) is not on PATH. EconAgent drives your own Claude Code login: https://docs.claude.com/claude-code"
if [ "${1:-}" = "--with-kronos" ]; then uv sync --extra dev --extra kronos; else uv sync --extra dev; fi
"$ROOT/scripts/install_vendors.sh" "$@"
chmod +x config/keys.sh scripts/*.sh
cat <<MSG

Setup done. Next:
  1. Add API keys (free tiers):   config/keys.sh set FRED_API_KEY   (see README for the list; on Linux export them as env vars)
  2. Make sure Claude Code is logged in:   claude   (then /exit)
  3. Run the tests:               .venv/bin/python -m pytest
  4. Start the app:               .venv/bin/python scripts/serve.py   ->  http://127.0.0.1:8750
MSG
