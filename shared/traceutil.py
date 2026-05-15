"""Pipeline trace utilities for correlating timing across SQS message hops.

Each consumer appends completed steps to a single JSON array under :data:`PIPELINE_TRACE_KEY`.
Every step records ``step``, ``start``, and ``end`` as UTC ISO-8601 timestamps (``Z`` suffix).
Optional per-step metrics (e.g. latency in ms) are merged into the same dict.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# JSON field name on ingest / pre-LLM / post-LLM / dispatch payloads for the ordered trace list.
PIPELINE_TRACE_KEY = "pipeline_trace"


def utc_iso() -> str:
    """Return the current instant in UTC as ISO 8601 with milliseconds and a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_pipeline_trace(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a defensive copy of ``msg``'s trace list, or an empty list if missing or invalid."""
    raw = msg.get(PIPELINE_TRACE_KEY)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for x in raw:
        if isinstance(x, dict):
            out.append(dict(x))
    return out


def step_duration_ms(start: str, end: str) -> float | None:
    """Compute elapsed milliseconds between two UTC ISO timestamps (``Z`` or ``+00:00``).

    Returns:
        Positive delta in ms, or ``None`` if either value is missing or unparsable.
    """
    try:
        s = (start or "").strip().replace("Z", "+00:00")
        e = (end or "").strip().replace("Z", "+00:00")
        t0 = datetime.fromisoformat(s)
        t1 = datetime.fromisoformat(e)
        return (t1 - t0).total_seconds() * 1000.0
    except (ValueError, TypeError):
        return None


def append_completed_step(
    prior: dict[str, Any],
    *,
    step: str,
    start: str,
    metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Append one completed pipeline step and return the full trace list.

    Preserves any existing steps from ``prior``'s ``pipeline_trace``, then appends a dict with
    ``step``, ``start``, ``end`` (``end`` is set to :func:`utc_iso` at call time). When ``metrics``
    is provided, each key/value pair is copied onto the new step; values that are ``None`` are
    skipped so callers can omit optional measurements cleanly.

    Args:
        prior: Message body dict that may already contain ``pipeline_trace``.
        step: Logical stage name (e.g. ``\"webhook\"``, ``\"intent\"``, ``\"llm_handler\"``).
        start: UTC ISO timestamp recorded when that stage began.
        metrics: Optional dict of extra fields (latencies, token counts, etc.).

    Returns:
        A new list to assign to the outbound message's ``pipeline_trace`` field.
    """
    trace = get_pipeline_trace(prior)
    entry: dict[str, Any] = {"step": step, "start": start, "end": utc_iso()}
    if metrics:
        for k, v in metrics.items():
            if v is not None:
                entry[k] = v
    trace.append(entry)
    return trace
