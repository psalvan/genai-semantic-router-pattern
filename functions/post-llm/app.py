"""Lambda: post-LLM SQS consumer — map ``llm_output`` to WhatsApp text and enqueue **dispatch-wpp**.

Logs the full post-LLM payload as one JSON line, appends a ``post_llm`` pipeline trace step, sends
user-facing strings to the outbound FIFO when ``phone_number`` is present, and emits a
multi-line **pipeline performance report** to CloudWatch (``INFO``) for observability on the full
LLM path.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3

from logutil import log_event
from ssmutil import clear_ssm_cache
from traceutil import PIPELINE_TRACE_KEY, append_completed_step, step_duration_ms, utc_iso

_sqs = boto3.client("sqs")
_perf_logger = logging.getLogger(__name__)

# User-visible line when the structured output requests a backend transaction (demo placeholder).
_EXECUTE_TRANSACTION_MESSAGE = "Aguarde enquanto realizo sua solicitação..."
# next_action values that map ``llm_output.user_response`` to an outbound WhatsApp text.
_USER_RESPONSE_ACTIONS = frozenset({"NEED_MORE_INFO", "PROVIDE_DIRECT_ANSWER"})
# All next_action labels handled without logging ``post_llm_unknown_next_action``.
_KNOWN_NEXT_ACTIONS = _USER_RESPONSE_ACTIONS | frozenset({"EXECUTE_TRANSACTION"})


def _sqs_fifo_token(value: str, *, max_len: int = 128) -> str:
    """FIFO MessageGroupId / MessageDeduplicationId: alphanumeric, hyphens, underscores only."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in (value or "").strip())
    out = cleaned or "na"
    return out[:max_len]


def _enqueue_dispatch_wpp_fifo(
    *,
    queue_url: str,
    customer_wa_id: str,
    business_sender_id: str,
    message: str,
    message_group_source: str,
    dedup_source: str,
    correlation_id: str | None = None,
    pipeline_trace: list[dict[str, Any]] | None = None,
) -> None:
    """Publish one outbound WhatsApp job to the FIFO queue consumed by ``dispatch-wpp``."""
    payload: dict[str, Any] = {
        "phone_number_id": customer_wa_id.strip(),
        "message": message,
    }
    if business_sender_id.strip():
        payload["sender_phone_number_id"] = business_sender_id.strip()
    if correlation_id and correlation_id.strip():
        payload["correlation_id"] = correlation_id.strip()
    if pipeline_trace is not None:
        payload[PIPELINE_TRACE_KEY] = pipeline_trace
    _sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(payload, ensure_ascii=False),
        MessageGroupId=_sqs_fifo_token(message_group_source),
        MessageDeduplicationId=_sqs_fifo_token(dedup_source),
    )


def print_performance_report(trace_data: list[dict[str, Any]]) -> None:
    """Log a human-readable pipeline timing summary to CloudWatch at INFO level.

    Expects ``trace_data`` in the same shape as ``pipeline_trace``: a list of dicts with at least
    ``step``, ``start``, and ``end``. Uses steps ``webhook``, ``intent``, ``llm_handler``, and
    ``post_llm`` plus optional numeric metrics on ``intent`` / ``llm_handler`` (see ``llm-handler``
    and ``intent-processor``). No-op if ``trace_data`` is empty.

    Note:
        Intent shortcut flows that skip post-LLM never invoke this function.
    """
    if not isinstance(trace_data, list) or not trace_data:
        return

    def _find_step(name: str) -> dict[str, Any] | None:
        """Return the first trace row whose ``step`` equals ``name``."""
        for row in trace_data:
            if isinstance(row, dict) and row.get("step") == name:
                return row
        return None

    def _step_wall_ms(row: dict[str, Any] | None) -> float:
        """Wall-clock duration in ms for a step from ``start``/``end``, or ``0.0`` if unknown."""
        if not row:
            return 0.0
        delta = step_duration_ms(str(row.get("start") or ""), str(row.get("end") or ""))
        return float(delta) if delta is not None else 0.0

    def _num(row: dict[str, Any] | None, key: str) -> float | None:
        """Parse a numeric metric from ``row[key]``; return ``None`` for missing or non-numeric."""
        if not row:
            return None
        v = row.get(key)
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    webhook = _find_step("webhook")
    intent = _find_step("intent")
    llm = _find_step("llm_handler")
    post = _find_step("post_llm")

    d_webhook = _step_wall_ms(webhook)
    d_intent = _step_wall_ms(intent)
    router_ms = _num(intent, "semantic_router_latency_ms")
    router_part = router_ms if router_ms is not None else 0.0
    ingest_prep_ms = d_webhook + max(0.0, d_intent - router_part)

    line2 = f"{router_ms:.1f} ms" if router_ms is not None else "N/A ms"

    rag_ms = _num(llm, "rag_s3_latency_ms")
    llm_e2e_ms = _num(llm, "llm_e2e_latency_ms")
    line3 = f"{rag_ms:.1f} ms" if rag_ms is not None else "N/A ms"

    model_id = "-"
    if llm:
        mid = llm.get("llm_model_id")
        if mid is not None and str(mid).strip():
            model_id = str(mid).strip()

    tps = _num(llm, "llm_tps")
    tpot = _num(llm, "llm_tpot_ms")
    tps_s = f"{tps:.2f}" if tps is not None else "-"
    tpot_s = f"{tpot:.4f}" if tpot is not None else "-"

    if llm:
        it = llm.get("llm_input_tokens")
        ot = llm.get("llm_output_tokens")
        it_s = str(it) if it is not None else "-"
        ot_s = str(ot) if ot is not None else "-"
        tok_line = f"{it_s} in / {ot_s} out"
    else:
        tok_line = "- in / - out"

    out_fmt_ms = _step_wall_ms(post)

    total_ms: float | None = None
    first = trace_data[0]
    last = trace_data[-1]
    if isinstance(first, dict) and isinstance(last, dict):
        total_ms = step_duration_ms(str(first.get("start") or ""), str(last.get("end") or ""))

    if total_ms is None or total_ms <= 0:
        parts_sum = ingest_prep_ms
        parts_sum += router_part
        parts_sum += rag_ms if rag_ms is not None else 0.0
        parts_sum += llm_e2e_ms if llm_e2e_ms is not None else 0.0
        parts_sum += out_fmt_ms
        total_ms = parts_sum

    llm_for_pct = llm_e2e_ms if llm_e2e_ms is not None else 0.0
    gargalo_pct = (100.0 * llm_for_pct / total_ms) if total_ms > 0 else 0.0

    if llm_e2e_ms is not None:
        line4_head = f"{llm_e2e_ms:.1f} ms   <-- (GARGALO: {gargalo_pct:.1f}% do tempo total)"
        e2e_line = f"{llm_e2e_ms:.1f} ms"
    else:
        line4_head = f"N/A ms   <-- (GARGALO: {gargalo_pct:.1f}% do tempo total)"
        e2e_line = "N/A ms"

    lines = [
        "=== PIPELINE PERFORMANCE REPORT ===",
        f"1. Ingest/Routing Prep:      {ingest_prep_ms:.1f} ms",
        f"2. Semantic Router (EC2):    {line2}",
        f"3. Dynamic RAG (S3 fetch):   {line3}",
        f"4. LLM Inference:            {line4_head}",
        f"   - Model:                 {model_id}",
        f"   - E2E Latency:           {e2e_line}",
        f"   - Speed:                 {tps_s} Tokens/s (TPS)",
        f"   - TPOT:                  {tpot_s} ms/token",
        f"   - Input/Output:          {tok_line}",
        f"5. Output Formatting:        {out_fmt_ms:.1f} ms",
        "===================================",
        f"Total Pipeline Time:         {total_ms:.1f} ms",
    ]
    _perf_logger.info("\n".join(lines))


def _outbound_text(next_action: str | None, llm_output: dict[str, Any]) -> str | None:
    """Resolve the WhatsApp text to send for ``next_action``, or ``None`` if nothing should be sent."""
    if next_action == "EXECUTE_TRANSACTION":
        return _EXECUTE_TRANSACTION_MESSAGE
    if next_action in _USER_RESPONSE_ACTIONS or not (next_action or "").strip():
        text = (llm_output.get("user_response") or "").strip()
        return text or None
    return None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process each post-LLM record: log, optional dispatch enqueue, performance report, partial failures."""
    del context
    clear_ssm_cache()
    failures: list[dict[str, str]] = []
    dispatch_url = (os.environ.get("DISPATCH_WPP_QUEUE_URL") or "").strip()

    for record in event.get("Records") or []:
        sqs_message_id = record.get("messageId") or ""
        correlation_id: str | None = None
        try:
            msg = json.loads(record.get("body") or "{}")
            correlation_id = (msg.get("correlation_id") or "").strip() or None
            trace_start = utc_iso()
            log_event("post_llm_received", correlation_id=correlation_id, message_id=sqs_message_id)
            line = json.dumps(msg, ensure_ascii=False, default=str)
            print(line)

            llm_raw = msg.get("llm_output")
            llm_output: dict[str, Any] = llm_raw if isinstance(llm_raw, dict) else {}
            next_action_raw = llm_output.get("next_action")
            next_action = str(next_action_raw).strip() if next_action_raw is not None else None

            if next_action and next_action not in _KNOWN_NEXT_ACTIONS:
                log_event(
                    "post_llm_unknown_next_action",
                    correlation_id=correlation_id,
                    next_action=next_action,
                )

            phone_number = (msg.get("phone_number") or "").strip()
            phone_number_id = (msg.get("phone_number_id") or "").strip()
            outbound = _outbound_text(next_action, llm_output)
            post_trace = append_completed_step(msg, step="post_llm", start=trace_start)

            if outbound and phone_number:
                if not dispatch_url:
                    log_event("post_llm_dispatch_queue_missing", correlation_id=correlation_id)
                    raise RuntimeError("DISPATCH_WPP_QUEUE_URL empty")
                group_src = phone_number if phone_number else (correlation_id or sqs_message_id)
                dedup_src = f"{correlation_id or 'na'}-postllm-{sqs_message_id}"
                _enqueue_dispatch_wpp_fifo(
                    queue_url=dispatch_url,
                    customer_wa_id=phone_number,
                    business_sender_id=phone_number_id,
                    message=outbound,
                    message_group_source=group_src,
                    dedup_source=dedup_src,
                    correlation_id=correlation_id,
                    pipeline_trace=post_trace,
                )
                log_event(
                    "post_llm_dispatch_enqueued",
                    correlation_id=correlation_id,
                    next_action=next_action or "default_user_response",
                    text_len=len(outbound),
                    pipeline_trace=post_trace,
                )
            elif outbound and not phone_number:
                log_event(
                    "post_llm_dispatch_skipped_no_recipient",
                    correlation_id=correlation_id,
                    next_action=next_action,
                    pipeline_trace=post_trace,
                )
            elif not outbound and phone_number:
                log_event(
                    "post_llm_dispatch_skipped_empty_message",
                    correlation_id=correlation_id,
                    next_action=next_action,
                    pipeline_trace=post_trace,
                )
            else:
                log_event(
                    "post_llm_no_dispatch",
                    correlation_id=correlation_id,
                    next_action=next_action,
                    pipeline_trace=post_trace,
                )
            print_performance_report(post_trace)
        except Exception as e:
            log_event("post_llm_error", correlation_id=correlation_id, error=str(e))
            if sqs_message_id:
                failures.append({"itemIdentifier": sqs_message_id})

    return {"batchItemFailures": failures}
