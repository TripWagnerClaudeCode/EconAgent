#!/bin/zsh
# Keychain helper for EconAgent API keys (service: econagent).
#   keys.sh set NAME     prompt for a value (hidden) and store it
#   keys.sh get NAME     print a value (used by the launcher)
#   keys.sh list         show which keys from .env.example are set (no values)
#   keys.sh env          print NAME=value lines for all set keys (for spawning MCP servers)
set -e
SERVICE=econagent
HERE=${0:A:h}
names() { grep -E '^[A-Z_]+=' "$HERE/.env.example" | cut -d= -f1; }

case "$1" in
  set)
    [ -n "$2" ] || { echo "usage: keys.sh set NAME" >&2; exit 2; }
    printf "Value for %s (input hidden): " "$2" >&2
    read -rs value; echo >&2
    security add-generic-password -U -s "$SERVICE" -a "$2" -w "$value"
    echo "stored $2" >&2
    ;;
  get)
    security find-generic-password -s "$SERVICE" -a "$2" -w 2>/dev/null
    ;;
  list)
    for n in $(names); do
      if security find-generic-password -s "$SERVICE" -a "$n" >/dev/null 2>&1; then echo "set      $n"; else echo "missing  $n"; fi
    done
    ;;
  env)
    for n in $(names); do
      v=$(security find-generic-password -s "$SERVICE" -a "$n" -w 2>/dev/null) || continue
      echo "$n=$v"
    done
    ;;
  *)
    echo "usage: keys.sh {set NAME|get NAME|list|env}" >&2; exit 2;;
esac
