#!/bin/bash
# update-status.sh - refresh current-status.json from live git state.
# Called from run.sh after each pass. Safe to run manually.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKTREE=/home/web/seaparser/worktrees/real-map-data
STATUS="$BASE/current-status.json"

if [ ! -d "$WORKTREE/.git" ] && [ ! -f "$WORKTREE/.git" ]; then
    echo "update-status: worktree missing: $WORKTREE" >&2
    exit 1
fi

branch=$(git -C "$WORKTREE" branch --show-current)
commit=$(git -C "$WORKTREE" rev-parse HEAD)
subject=$(git -C "$WORKTREE" log -1 --format='%s')
remote_sha=$(git -C "$WORKTREE" rev-parse "origin/$branch" 2>/dev/null || echo "")

if [ "$commit" = "$remote_sha" ]; then
    synced=true
else
    synced=false
fi

mapfile -t dirty < <(git -C "$WORKTREE" status --porcelain | awk '{print $2}')
dirty_json="[]"
if [ ${#dirty[@]} -gt 0 ]; then
    dirty_json=$(printf '%s\n' "${dirty[@]}" | python3 -c '
import json, sys
print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))
')
fi

timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 - "$STATUS" "$timestamp" "$WORKTREE" "$branch" "$commit" "$subject" "$synced" "$dirty_json" <<'PYEOF'
import json, sys
status_path, ts, wt, br, cm, sb, sync, dirty_json = sys.argv[1:]
old = {}
try:
    old = json.loads(open(status_path).read())
except Exception:
    pass
new = {
    "version": 2,
    "updated_at": ts,
    "worktree": wt,
    "branch": br,
    "commit": cm,
    "commit_subject": sb,
    "remote_synced": sync == "true",
    "uncommitted_files": json.loads(dirty_json),
    "last_ci": old.get("last_ci"),
    "published_static": old.get("published_static"),
    "api_status": old.get("api_status"),
    "active_session": old.get("active_session"),
    "next_task": old.get("next_task"),
    "notes": old.get("notes", "See HANDOFF.md for full narrative."),
}
open(status_path, "w").write(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
print(f"updated {status_path}")
PYEOF
