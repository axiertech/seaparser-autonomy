#!/bin/bash
set -euo pipefail
umask 077
BASE=/home/web/seaparser/autonomy
WORKDIR=/home/web/seaparser/worktrees/real-map-data
CODEX=/home/web/seaparser/.local/bin/codex
MODE=${1:?probe eller work}
END_AT=${2:?explicit sluttid krävs, ISO-8601}
SESSION_ID=${3:-}
[[ $(id -un) == seaparser ]] || exit 77
[[ $MODE == probe || $MODE == work ]] || exit 64
END_EPOCH=$(date -d "$END_AT" +%s)
REMAINING=$((END_EPOCH-$(date +%s)))
(( REMAINING > 0 )) || exit 124
exec 9>"$BASE/run.lock"
flock -n 9 || exit 75
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)-$$
LOG="$BASE/logs/$RUN_ID.log"
exec >>"$LOG" 2>&1
finish() { rc=$?; trap - EXIT; printf 'END=%s EXIT_CODE=%s\n' "$(date -u +%FT%TZ)" "$rc"; printf '%s\n' "$rc" >"$BASE/logs/$RUN_ID.exit"; if [[ -f "$BASE/runner.pid" && $(cat "$BASE/runner.pid") == "$$" ]]; then rm -- "$BASE/runner.pid"; fi; }
trap finish EXIT
printf 'START=%s MODE=%s PID=%s CWD=%s SESSION_ID=%s END_AT=%s\n' "$(date -u +%FT%TZ)" "$MODE" "$$" "$WORKDIR" "$SESSION_ID" "$END_AT"
printf '%s\n' "$$" >"$BASE/runner.pid"
# Rotate logs once per pass. Failure must not stop the run.
if [[ -x "$BASE/rotate-logs.sh" ]]; then
    "$BASE/rotate-logs.sh" || echo "varning: rotate-logs.sh misslyckades" >&2
fi
cd "$WORKDIR"
export GIT_OPTIONAL_LOCKS=0
if [[ $MODE == probe ]]; then
    git status --short --branch
    git log -1 --format='%h %s'
    sha256sum AGENTS.md
    # Bounded delay makes disconnect and concurrency tests possible.
    timeout --signal=TERM --kill-after=5s "${REMAINING}s" sleep 20
else
    [[ $SESSION_ID =~ ^[0-9a-f-]{36}$ ]] || exit 64
    # Existing forecast worker must be stopped first. Its session ID is explicitly bound here.
    [[ -f "$BASE/handoff.session" ]] || { echo 'BLOCKED: forecast handoff missing'; exit 78; }
    [[ $(cat "$BASE/handoff.session") == "$SESSION_ID" ]] || exit 78
    [[ $(git branch --show-current) == development/* ]] || exit 77
    # No forecast work until the actual pinned SSH-disconnect probe passed.
    /usr/bin/python3 -B - <<'PYVERIFY'
import json
from pathlib import Path
p = Path('/home/web/seaparser/autonomy/logs/ssh-probe-bd5600c/latest.json')
try:
    r = json.loads(p.read_text())
    expected = 'bd5600c602a8896dbfbcf6b185729ce44483d2e4'
    assert r['mode'] == 'ssh-disconnect-proof'
    assert r['exit_code'] == 0 and r['ssh_persistence_verified'] is True
    assert r['expected_commit'] == expected
    assert r['workdir'] == '/home/web/seaparser/worktrees/real-map-data'
    assert r['before_disconnect']['commit'] == expected
    assert r['after_disconnect']['commit'] == expected
    assert r['disconnect_observed_at'] and r['finished_at']
except (OSError, ValueError, KeyError, AssertionError):
    raise SystemExit('BLOCKED: pinned SSH-disconnect probe has not been verified')
PYVERIFY
    "$CODEX" login status
    REMAINING=$((END_EPOCH-$(date +%s)))
    (( REMAINING > 0 )) || exit 124
    timeout --signal=TERM --kill-after=30s "${REMAINING}s" "$CODEX" exec -C "$WORKDIR" -s workspace-write -c 'approval_policy="never"' --json resume "$SESSION_ID" "$(cat "$BASE/continue.txt") Sluttid: $END_AT."
fi
