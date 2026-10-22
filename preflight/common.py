"""Common interface for preflight measurements.

The repository previously carried ten one-off ``*preflight*.py`` scripts
(trelleborg-*, marine-*, met-*, stream-land-*, water-stage-*,
compact-source-*). Each script hard-coded its own RSS budget, its own
``resource.setrlimit`` calls and its own output format, so results could not
be compared, reused or tested in isolation.

This module replaces that spread with one schema: every check is a plain
callable executed through :func:`run`, which returns a frozen
:class:`CheckResult` carrying the check name, success flag, wall-clock
seconds, peak RSS and either structured details or an error string. Because
the measurement wrapper is separate from the check body, future checks are
testable in isolation without spawning a subprocess or touching the network.
"""
from __future__ import annotations

import resource
import time
from dataclasses import dataclass
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


def run(name: str, fn: Callable[[], Any], *,
        budget_mib: int | None = None) -> CheckResult:
    """Execute ``fn`` and measure its duration and peak RSS.

    Args:
        name: Human-readable check name, copied into the result.
        fn: Zero-argument callable performing the check. Its return value is
            stored in ``details`` (dicts are kept as-is, other values are
            wrapped as ``{"value": ...}``).
        budget_mib: Optional peak-RSS budget in MiB. When given, the check is
            marked not ok if the measured peak RSS exceeds it.

    Returns:
        A frozen :class:`CheckResult`. Exceptions raised by ``fn`` are caught
        and reported through ``error`` with ``ok=False``; they are never
        re-raised.
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
