#!/bin/bash
# rotate-logs.sh – roterar loggar och komprimerar gamla turn-filer.
# Anropas från run.sh (eller cron). Kräver ingen root.
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGROOT="$BASE/logs"
URLS="$BASE/published-urls.log"
MAX_LOG_AGE_DAYS=30
MAX_DIR_AGE_DAYS=90
MAX_URLS_BYTES=$((10 * 1024 * 1024))   # 10 MiB

# 1. Rotera published-urls.log vid storlek
if [ -f "$URLS" ]; then
    size=$(stat -c%s "$URLS" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_URLS_BYTES" ]; then
        ts=$(date -u +%Y%m%dT%H%M%SZ)
        mv "$URLS" "$BASE/published-urls-$ts.log"
        gzip -f "$BASE/published-urls-$ts.log"
        : > "$URLS"   # ny tom fil
        echo "roterade published-urls.log ($size byte) -> published-urls-$ts.log.gz"
    fi
fi

# 2. Komprimera turn-*.jsonl och turn-*.stderr.log äldre än 30 dagar
find "$LOGROOT" -type f \( -name 'turn-*.jsonl' -o -name 'turn-*.stderr.log' \) \
    -mtime +"$MAX_LOG_AGE_DAYS" -print0 2>/dev/null \
  | while IFS= read -r -d '' f; do
      gzip -f "$f" && echo "gzip: $f"
    done

# 3. Rensa loggkataloger äldre än 90 dagar, men behåll final-report.*
find "$LOGROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$MAX_DIR_AGE_DAYS" 2>/dev/null \
  | while IFS= read -r d; do
      if [ -f "$d/final-report.md" ] || [ -f "$d/final-report.json" ]; then
          # Behåll sammanfattningen, ta bort övriga filer
          find "$d" -type f ! -name 'final-report.*' -delete 2>/dev/null || true
          echo "rensade (behåll final-report): $d"
      else
          rm -rf "$d"
          echo "tog bort: $d"
      fi
    done

echo "rotate-logs.sh klar: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
