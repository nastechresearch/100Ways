#!/usr/bin/env bash
set -euo pipefail

url="$1"
destination="$2"
branch="${3:-}"
max_attempts="${FETCH_MAX_ATTEMPTS:-4}"

rm -rf "$destination"
for attempt in $(seq 1 "$max_attempts"); do
  rm -rf "$destination"
  clone_args=(--depth=1 --no-tags --filter=blob:none --single-branch)
  if [[ -n "$branch" ]]; then
    clone_args+=(--branch "$branch")
  fi
  if git -c http.version=HTTP/1.1 -c protocol.version=2 clone "${clone_args[@]}" "$url" "$destination"; then
    exit 0
  fi
  if [[ "$attempt" -lt "$max_attempts" ]]; then
    sleep_seconds=$((attempt * attempt * 5))
    echo "direct fetch failed; retrying in ${sleep_seconds}s (attempt ${attempt}/${max_attempts})" >&2
    sleep "$sleep_seconds"
  fi
done

echo "direct public fetch failed after ${max_attempts} attempts" >&2
exit 1
