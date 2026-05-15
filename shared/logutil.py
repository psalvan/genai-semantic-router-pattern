"""Structured one-line JSON logging for Lambdas and local scripts (CloudWatch Logs Insights friendly).

Each call emits a single JSON object on stdout with a ``phase`` string, ``ts_epoch``, optional
``correlation_id``, and caller-supplied fields (``None`` values are omitted).

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import json
import time
from typing import Any


def log_event(phase: str, correlation_id: str | None = None, **fields: Any) -> None:
    """Emit a single JSON log record with optional correlation_id and arbitrary fields."""
    record: dict[str, Any] = {"phase": phase, "ts_epoch": time.time()}
    if correlation_id:
        record["correlation_id"] = correlation_id
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    print(json.dumps(record, ensure_ascii=False, default=str))
