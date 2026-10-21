#!/bin/bash
set -euo pipefail
umask 077
[[ $(id -un) == seaparser ]] || exit 77
exec /usr/bin/python3 -B /home/web/seaparser/autonomy/ssh_probe.py
