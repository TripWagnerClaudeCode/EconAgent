#!/usr/bin/env python3
"""Refuse to commit secrets.

  check_secrets.py          scan what is staged (used by the pre-commit hook)
  check_secrets.py --all    scan every tracked file with the generic patterns (used by CI, which has no keys)

It looks for (1) the actual values of your keys (macOS Keychain "econagent" service and same-named environment variables),
including their first/last 8 characters, and (2) generic `api_key = <long token>` assignments. It prints the file, line and
key NAME of a hit, never the value. Exit code 1 means: do not commit.
"""
from __future__ import annotations

import os
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import mcp_config  # noqa: E402

GENERIC = re.compile(r"""(?i)(api[_-]?key|secret|token|passw(?:or)?d)["' ]*[:=]["' ]*[A-Za-z0-9_\-]{24,}""")
ALLOW = (re.compile(r"SUPERSECRETKEY123"), re.compile(r"api_key=\*\*\*"))   # fixtures that prove redaction works


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout  # noqa: S603,S607


def key_values() -> list[tuple[str, str]]:
    """(label, value) for every key we can find. A list, not a dict: the Keychain and the environment may hold different
    values for the same name and BOTH must be checked."""
    names = [ln.split("=", 1)[0] for ln in (ROOT / "config" / ".env.example").read_text().splitlines() if re.match(r"^[A-Z_]+=", ln)]
    out: list[tuple[str, str]] = []
    for n in names:
        for v in {mcp_config.keychain_get(n), os.environ.get(n)}:
            if v and len(v) >= 12:
                out.append((n, v))
                if len(v) >= 20:
                    out += [(f"{n} (first 8 chars)", v[:8]), (f"{n} (last 8 chars)", v[-8:])]
    return out


def added_lines(all_files: bool):
    if all_files:
        for f in git("ls-files").splitlines():
            p = ROOT / f
            if p.is_file() and not f.startswith("web/vendor/"):
                for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                    yield f, i, line
        return
    cur, no = "?", 0
    for line in git("diff", "--cached", "--no-color", "-U0").splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            no = int(m.group(1)) - 1 if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            no += 1
            if not cur.startswith("web/vendor/"):
                yield cur, no, line[1:]


def main() -> int:
    all_files = "--all" in sys.argv
    values = [] if all_files else key_values()
    hits = []
    for f, i, line in added_lines(all_files):
        for name, v in values:
            if v in line:
                hits.append((f, i, f"contains your {name}"))
        if GENERIC.search(line) and not any(a.search(line) for a in ALLOW):
            hits.append((f, i, "looks like a hard-coded secret"))
    for f, i, why in hits:
        print(f"BLOCKED  {f}:{i}  {why}", file=sys.stderr)
    if hits:
        print("\nNothing was committed. Remove the secret from the file (keys belong in the Keychain or the environment).", file=sys.stderr)
        return 1
    print(f"secret scan ok ({'all tracked files' if all_files else 'staged changes'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
