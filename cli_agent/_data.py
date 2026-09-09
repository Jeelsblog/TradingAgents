"""Shared helpers for the CLI-agent data layer (bundle + MCP server).

All calls go through the framework's own ``route_to_vendor`` / dataflow
functions, so the point-in-time look-ahead guards apply here too. Nothing in
this package makes an LLM call.
"""

from __future__ import annotations

import datetime as dt
import threading

CALL_TIMEOUT_S = 30  # a slow/unreachable vendor never hangs the caller


def guarded(label: str, fn) -> str:
    """Run a blocking data pull in a daemon watchdog thread.

    Returns the call's text output, a visible ``unavailable`` note if it
    raised, or a ``timed out`` note if it outran ``CALL_TIMEOUT_S``. The
    error text is itself useful signal (e.g. "FRED key missing", "no news"),
    so it is surfaced rather than swallowed.
    """
    box: dict[str, str] = {}

    def _run() -> None:
        try:
            out = fn()
            box["ok"] = str(out).strip() or f"_{label}: empty result_"
        except Exception as exc:  # noqa: BLE001 - the error is the signal
            box["ok"] = f"_{label}: unavailable -- {type(exc).__name__}: {exc}_"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(CALL_TIMEOUT_S)
    if t.is_alive():
        return f"_{label}: timed out after {CALL_TIMEOUT_S}s (vendor slow/unreachable)_"
    return box.get("ok", f"_{label}: no result_")


def daterange(curr_date: str, days: int) -> tuple[str, str]:
    """(start, end) ISO dates for a trailing window ending at ``curr_date``."""
    end = dt.date.fromisoformat(curr_date)
    start = end - dt.timedelta(days=days)
    return start.isoformat(), end.isoformat()
