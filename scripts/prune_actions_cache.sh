#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 CACHE_PATH MAX_MB [--strategy oldest|reset] [--protect PATH ...]" >&2
}

if [ "$#" -lt 2 ]; then
  usage
  exit 2
fi

CACHE_PATH=$1
MAX_MB=$2
shift 2

STRATEGY=oldest
PROTECTED_PATHS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --strategy)
      if [ "$#" -lt 2 ]; then
        usage
        exit 2
      fi
      STRATEGY=$2
      shift 2
      ;;
    --protect)
      if [ "$#" -lt 2 ]; then
        usage
        exit 2
      fi
      PROTECTED_PATHS+=("$2")
      shift 2
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

case "$MAX_MB" in
  ''|*[!0-9]*)
    echo "::error::MAX_MB must be a positive integer"
    exit 2
    ;;
esac

case "$STRATEGY" in
  oldest|reset) ;;
  *)
    echo "::error::Unknown cache pruning strategy: ${STRATEGY}"
    exit 2
    ;;
esac

mkdir -p "$CACHE_PATH"
MAX_KB=$((MAX_MB * 1024))

cache_size_kb() {
  du -sk "$CACHE_PATH" 2>/dev/null | awk '{ print $1 + 0 }'
}

entry_mtime() {
  if stat -c '%Y' "$1" >/dev/null 2>&1; then
    stat -c '%Y' "$1"
  else
    stat -f '%m' "$1"
  fi
}

is_protected() {
  local entry=$1
  local protected
  if [ "${#PROTECTED_PATHS[@]}" -eq 0 ]; then
    return 1
  fi
  for protected in "${PROTECTED_PATHS[@]}"; do
    [ -n "$protected" ] || continue
    case "$entry" in
      "$protected"|"$protected"/*)
        return 0
        ;;
    esac
    case "$protected" in
      "$entry"|"$entry"/*)
        return 0
        ;;
    esac
  done
  return 1
}

remove_cache_contents() {
  local entry
  for entry in "$CACHE_PATH"/* "$CACHE_PATH"/.[!.]* "$CACHE_PATH"/..?*; do
    [ -e "$entry" ] || continue
    if is_protected "$entry"; then
      continue
    fi
    rm -rf "$entry"
  done
}

oldest_unprotected_entry() {
  local entry
  local oldest=
  local oldest_mtime=
  local mtime

  for entry in "$CACHE_PATH"/* "$CACHE_PATH"/.[!.]* "$CACHE_PATH"/..?*; do
    [ -e "$entry" ] || continue
    if is_protected "$entry"; then
      continue
    fi
    mtime=$(entry_mtime "$entry")
    if [ -z "$oldest" ] || [ "$mtime" -lt "$oldest_mtime" ]; then
      oldest=$entry
      oldest_mtime=$mtime
    fi
  done

  printf '%s\n' "$oldest"
}

CURRENT_KB=$(cache_size_kb)
if [ "$CURRENT_KB" -le "$MAX_KB" ]; then
  echo "Cache ${CACHE_PATH} is ${CURRENT_KB}KB, within ${MAX_KB}KB."
  exit 0
fi

echo "Cache ${CACHE_PATH} is ${CURRENT_KB}KB, pruning to ${MAX_KB}KB."

if [ "$STRATEGY" = "reset" ]; then
  remove_cache_contents
else
  while [ "$(cache_size_kb)" -gt "$MAX_KB" ]; do
    ENTRY=$(oldest_unprotected_entry)
    if [ -z "$ENTRY" ]; then
      echo "::warning::Cache ${CACHE_PATH} is still above ${MAX_MB}MB, but only protected entries remain."
      break
    fi
    rm -rf "$ENTRY"
  done
fi

FINAL_KB=$(cache_size_kb)
echo "Cache ${CACHE_PATH} is now ${FINAL_KB}KB."
