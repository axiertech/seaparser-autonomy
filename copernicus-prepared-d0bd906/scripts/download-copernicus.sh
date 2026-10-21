#!/bin/bash
set -euo pipefail
umask 077
WORKTREE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BASE=/home/web/seaparser/autonomy/copernicus
mkdir -p "$BASE"
exec 9>"$BASE/download.lock"
flock -n 9 || exit 75
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)-$$
OUT="$BASE/$RUN_ID"
LOG="$BASE/$RUN_ID.log"
printf 'Log: %s\nOutput: %s\n' "$LOG" "$OUT"
exec >>"$LOG" 2>&1
finish() { rc=$?; trap - EXIT; printf 'END=%s EXIT_CODE=%s\n' "$(date -u +%FT%TZ)" "$rc"; printf '%s\n' "$rc" >"$BASE/$RUN_ID.exit"; }
trap finish EXIT
printf 'START=%s CWD=%s END_DEADLINE=%s\n' "$(date -u +%FT%TZ)" "$WORKTREE" "$(date -u -d '+10 minutes' +%FT%TZ)"
cd "$WORKTREE"
# Force saved credential discovery rather than inherited credential values.
unset COPERNICUSMARINE_SERVICE_USERNAME COPERNICUSMARINE_SERVICE_PASSWORD
timeout --signal=TERM --kill-after=10s 570s "/tmp/seaparser-real-map-data/.venv/bin/python" -B -m scripts.download_copernicus --output "$OUT" </dev/null
timeout --signal=TERM --kill-after=5s 20s "/tmp/seaparser-real-map-data/.venv/bin/python" -B -m scripts.audit_copernicus "$OUT"
