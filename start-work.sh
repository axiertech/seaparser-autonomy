#!/bin/bash
set -euo pipefail
umask 077
exec /usr/bin/python3 -B /home/web/seaparser/autonomy/work_session.py "$@"
