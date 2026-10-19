#!/usr/bin/env bash
# Install the pre-commit hook that blocks commits containing your API keys.  Run once after cloning.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cat > "$ROOT/.git/hooks/pre-commit" <<'HOOK'
#!/bin/sh
ROOT="$(git rev-parse --show-toplevel)"
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/scripts/check_secrets.py"
HOOK
chmod +x "$ROOT/.git/hooks/pre-commit"
echo "pre-commit secret scan installed."
