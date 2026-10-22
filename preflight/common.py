"""Common interface for preflight measurements.

The repository previously carried thirteen one-off ``*preflight*.py``
scripts (trelleborg-*, marine-*, met-*, stream-land-*, water-stage-*,
compact-source-*, compare-*). Each script hard-coded its own RSS budget,
its own ``resource.setrlimit`` calls and its own output format, so results
could not be compared, reused or tested in isolation.

This module replaces that spread with one schema: every check is a plain
callable executed through :func:`run`, which returns a frozen
:class:`CheckResult` carrying the check name, success flag, wall-clock
seconds, peak RSS and either structured details or an error string.

:func:`set_limits` applies the same rlimit and BLAS-thread pinning the
original scripts used, and :func:`emit` writes a one-line JSON result to
stdout with ``status``, ``name``, ``seconds``, ``peak_rss_kib``,
``details`` and ``error`` fields.
"""
from __future__ import annotations

import faulthandler
import json
import os
import resource
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

MIB_IN_KIB = 1024


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single preflight check."""

    name: str
    ok: bool
    seconds: float
    peak_rss_kib: int | None = None
    details: dict | None = None
    error: str | None = None


def _peak_rss_kib() -> int | None:
    """Return the process peak RSS in KiB, or None if unavailable."""
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, OSError, ValueError):
        return None


def _as_details(value: Any) -> dict | None:
    """Normalise a check's return value into a details dict or None."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return {"value": value}


def set_limits(*, budget_mib: int, wall_seconds: int | None = None,
               single_thread: bool = True) -> None:
    """Apply the process limits the original preflight scripts used.

    Args:
        budget_mib: Hard RLIMIT_AS cap in MiB. Both soft and hard limit
            are set to the same value.
        wall_seconds: Optional interval for faulthandler traceback dumps.
            The original scripts used 45 or similar values to catch hangs.
        single_thread: When True, pin OMP_NUM_THREADS and
            OPENBLAS_NUM_THREADS to 1 if they are not already set. This
            makes RSS measurements comparable between runs.

    Raises:
        ValueError: If budget_mib is negative.
        OSError: If the kernel rejects the new limit.
    """
    if budget_mib < 0:
        raise ValueError(f"budget_mib must be >= 0, got {budget_mib}")
    if single_thread:
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    limit_bytes = budget_mib * MIB_IN_KIB * 1024
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard != resource.RLIM_INFINITY and limit_bytes > hard:
        limit_bytes = hard
    # Only the soft limit is set. The kernel still refuses allocations
    # above it, but the process (or a later test) can raise the soft
    # limit again because the hard limit is left untouched. The original
    # one-off scripts set both limits, which made the process
    # permanently capped - fine for a short-lived script, wrong for a
    # library.
    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
    if wall_seconds is not None:
        faulthandler.dump_traceback_later(wall_seconds, repeat=True)


def emit(result: CheckResult) -> None:
    """Write the CheckResult as one-line JSON to stdout, flushed.

    The payload has the stable schema shared by every migrated check:

    - status: "ok" or "failed"
    - name: the check name
    - seconds: wall-clock duration, rounded to three decimals
    - peak_rss_kib: process peak RSS in KiB, or null
    - details: the check's own result dict, when present
    - error: the error string, when present
    """
    payload: dict[str, Any] = {
        "status": "ok" if result.ok else "failed",
        "name": result.name,
        "seconds": round(result.seconds, 3),
        "peak_rss_kib": result.peak_rss_kib,
    }
    if result.details is not None:
        payload["details"] = result.details
    if result.error is not None:
        payload["error"] = result.error
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def run(name: str, fn: Callable[[], Any], *,
        budget_mib: int | None = None) -> CheckResult:
    """Execute ``fn`` and measure its duration and peak RSS.

    Args:
        name: Human-readable check name, copied into the result.
        fn: Zero-argument callable performing the check. Its return value
            is stored in ``details`` (dicts are kept as-is, other values
            are wrapped as ``{"value": ...}``).
        budget_mib: Optional peak-RSS budget in MiB. When given, the check
            is marked not ok if the measured peak RSS exceeds it.

    Returns:
        A frozen :class:`CheckResult`. Exceptions raised by ``fn`` are
        caught and reported through ``error`` with ``ok=False``; they are
        never re-raised.
    """
    started = time.perf_counter()
    details: dict | None = None
    error: str | None = None
    try:
        details = _as_details(fn())
    except Exception as exc:  # noqa: BLE001 - every failure becomes a result
        error = f"{type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - started
    peak_rss_kib = _peak_rss_kib()

    ok = error is None
    if ok and budget_mib is not None and peak_rss_kib is not None:
        if peak_rss_kib > budget_mib * MIB_IN_KIB:
            ok = False
            error = (
                f"rss_budget_exceeded: {peak_rss_kib} KiB > "
                f"{budget_mib} MiB"
            )

    return CheckResult(
        name=name,
        ok=ok,
        seconds=seconds,
        peak_rss_kib=peak_rss_kib,
        details=details,
        error=error,
    )


def read_autonomy_conf(path: Path | None = None) -> dict[str, str]:
    """Read autonomy.conf via work_session.load_autonomy_conf.

    Imported lazily so that importing preflight.common does not pull in
    work_session (which reads autonomy.conf at import time).

    Args:
        path: Optional explicit config path; defaults to the autonomy.conf
            next to this package's parent.

    Returns:
        Dict of KEY=value strings.
    """
    from work_session import load_autonomy_conf
    if path is None:
        path = Path(__file__).resolve().parents[1] / "autonomy.conf"
    return load_autonomy_conf(path)
