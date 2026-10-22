"""Preflight check for the coastal water build.

Migrated from archive/preflight/water-stage-preflight.py. The original
script hard-coded OMP/BLAS thread pinning, RLIMIT_AS 1100 MiB and a
45-second faulthandler, then ran scripts.build_coastal_water.build and
printed a JSON object with stage/seconds/peak_rss_kib. This version uses
preflight.common.set_limits and preflight.common.run so the schema and
limits are the same as every other migrated check.

The worktree path is read from autonomy.conf (WORKDIR) instead of being
hard-coded, so the check follows the configured worktree.
"""
from __future__ import annotations

import sys

from preflight.common import emit, read_autonomy_conf, run, set_limits

DEFAULT_WORKDIR = "/home/web/seaparser/worktrees/real-map-data"
DATA_DIR = "/home/web/seaparser/data/trelleborg-stromstad-20261019"
BUDGET_MIB = 1100
WALL_SECONDS = 45


def check(stage: str) -> dict:
    """Run the coastal water build for the given stage.

    Args:
        stage: Stage name passed to build(); for example "topology" or
            "erosion".

    Returns:
        Dict with the stage name, stored in CheckResult.details.
    """
    conf = read_autonomy_conf()
    workdir = conf.get("WORKDIR", DEFAULT_WORKDIR)
    if workdir not in sys.path:
        sys.path.insert(0, workdir)
    from scripts.build_coastal_water import build
    build(DATA_DIR, stage)
    return {"stage": stage}


def main(argv: list[str] | None = None) -> int:
    """Run the check, emit JSON, return 0 on success and 1 on failure.

    Args:
        argv: Argument list; defaults to sys.argv.

    Returns:
        Process exit code: 0 ok, 1 check failed, 64 usage error.
    """
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        print("usage: python -m preflight.checks.water_stage <stage>",
              file=sys.stderr)
        return 64
    stage = argv[1]
    set_limits(budget_mib=BUDGET_MIB, wall_seconds=WALL_SECONDS)
    result = run("water_stage", lambda: check(stage))
    emit(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
