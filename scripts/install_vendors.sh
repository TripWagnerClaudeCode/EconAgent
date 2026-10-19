#!/usr/bin/env bash
# Clone, pin, build and patch the third-party MCP servers listed in scripts/vendors.txt. Idempotent.
#   scripts/install_vendors.sh                 core servers
#   scripts/install_vendors.sh --with-kronos   also clone the optional Kronos model code
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor"; mkdir -p "$VENDOR"
WITH_KRONOS=0; [ "${1:-}" = "--with-kronos" ] && WITH_KRONOS=1

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing prerequisite: $1  ($2)" >&2; exit 1; }; }
need git "install git"; need uv "https://docs.astral.sh/uv/"; need node "install Node.js 20+ (e.g. brew install node)"; need npm "ships with Node.js"

fetch_pinned() { # name repo commit
  local dir="$VENDOR/$1"
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$3" ]; then return 0; fi
  rm -rf "$dir"; mkdir -p "$dir"
  git -C "$dir" init -q; git -C "$dir" remote add origin "$2"
  git -C "$dir" fetch -q --depth 1 origin "$3"
  git -C "$dir" checkout -q FETCH_HEAD
  [ "$(git -C "$dir" rev-parse HEAD)" = "$3" ] || { echo "commit mismatch for $1" >&2; exit 1; }
}

grep -vE '^\s*(#|$)' "$ROOT/scripts/vendors.txt" | while IFS='|' read -r name repo commit build; do
  name=$(echo "$name" | xargs); repo=$(echo "$repo" | xargs); commit=$(echo "$commit" | xargs); build=$(echo "$build" | xargs)
  if [ "$build" = "optional" ] && [ "$WITH_KRONOS" = 0 ]; then echo "- skipping $name (optional; pass --with-kronos)"; continue; fi
  echo "== $name @ ${commit:0:8}"
  fetch_pinned "$name" "$repo" "$commit"
  d="$VENDOR/$name"
  case "$build" in
    uv)               (cd "$d" && uv sync --python 3.12 -q) ;;
    npm:*)            (cd "$d" && npm ci --ignore-scripts --no-audit --no-fund --silent && npm run "${build#npm:}" --silent) ;;
    pyvenv+patch)
      if git -C "$d" apply --reverse --check "$ROOT/patches/$name.patch" 2>/dev/null; then echo "   patch already applied"
      else git -C "$d" apply "$ROOT/patches/$name.patch" && echo "   applied patches/$name.patch"; fi
      (cd "$d" && uv venv --python 3.12 -q .venv && uv pip install -q --python .venv/bin/python -r requirements.txt "mcp[cli]>=1.20,<2" pydantic) ;;
    optional)         : ;;
  esac
done
echo "vendors ready."
