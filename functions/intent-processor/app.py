"""Lambda: ingest SQS consumer — semantic router HTTP classify, then pre-LLM or WhatsApp dispatch shortcut.

Calls the configurable semantic-router service with the user text, measures router HTTP latency, and
records it on the ``intent`` step of ``pipeline_trace``. Routes **ChitChat** and **Unknown** to a
fixed template reply on the **dispatch-wpp** FIFO queue (skips LLM). All other intents may send a
typing indicator (when webhook metadata is present), then forward ``correlation_id``, ``text``,
``intent``, phone fields, and the updated trace to **pre-LLM** SQS.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
import httpx

from logutil import log_event
from ssmutil import clear_ssm_cache, get_parameter
from traceutil import PIPELINE_TRACE_KEY, append_completed_step, utc_iso
from wpputil import send_typing_indicator

_sqs = boto3.client("sqs")

# Template replies for intents that skip the LLM (dispatched straight to WhatsApp from IntentProcessor).
_INTENT_QUICK_REPLY: dict[str, str] = {
    "ChitChat": "Olá.. quem que posso ajuda-lo?",
    "Unknown": (
        "Não compreendi ao certo o que você deseja fazer? Eu consigo lidar com "
        "PIX/Transferências, Fraude e dúvidas gerais sobre sua conta/cartão."
    ),
}


def _intent_suggested_label(intent: Any) -> str | None:
    """Return stripped ``intent_suggested`` from the router JSON, or ``None`` if missing or invalid."""
    if not isinstance(intent, dict):
        return None
    raw = intent.get("intent_suggested")
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


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
    """Publish outbound WhatsApp job to the FIFO dispatch queue (see `functions/dispatch-wpp/app.py`)."""
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


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process each ingest SQS record: router call, trace/metrics, pre-LLM or dispatch shortcut.

    Returns partial batch failure identifiers for records that raised so SQS can retry them.
    """
    del context
    clear_ssm_cache()
    failures: list[dict[str, str]] = []
    pre_url = (os.environ.get("PRELLM_QUEUE_URL") or "").strip()
    dispatch_url = (os.environ.get("DISPATCH_WPP_QUEUE_URL") or "").strip()

    for record in event.get("Records") or []:
        sqs_message_id = record.get("messageId") or ""
        correlation_id: str | None = None
        try:
            raw = record.get("body") or "{}"
            msg = json.loads(raw)
            correlation_id = (msg.get("correlation_id") or "").strip() or None
            text = (msg.get("text") or "").strip()
            phone_number = (msg.get("phone_number") or "").strip()
            phone_number_id = (msg.get("phone_number_id") or "").strip()
            whatsapp_message_id = (msg.get("whatsapp_message_id") or "").strip()
            log_event(
                "sqs_ingest_received",
                correlation_id=correlation_id,
                message_id=sqs_message_id,
                text_len=len(text),
                has_phone=bool(phone_number),
                has_phone_number_id=bool(phone_number_id),
            )
            if not correlation_id or not text:
                log_event("invalid_ingest_payload", correlation_id=correlation_id)
                continue

            trace_start = utc_iso()
            router_url = get_parameter("SMART_ROUTER_URL").strip()
            key = get_parameter("SMART_ROUTER_KEY", decrypt=True).strip()
            if not router_url:
                log_event("router_url_missing", correlation_id=correlation_id)
                raise RuntimeError("SMART_ROUTER_URL empty")

            headers = {"content-type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"

            log_event("router_request", correlation_id=correlation_id, url_host=router_url.split("/")[2])
            with httpx.Client(timeout=60.0) as client:
                _t_router0 = time.perf_counter()
                router_response = client.post(router_url, json={"text": text}, headers=headers)
                semantic_router_latency_ms = round((time.perf_counter() - _t_router0) * 1000.0, 3)
            router_response.raise_for_status()
            intent = router_response.json()
            log_event("router_response", correlation_id=correlation_id, intent_topic=intent.get("intent_suggested"))

            label = _intent_suggested_label(intent)
            quick = _INTENT_QUICK_REPLY.get(label or "")
            if quick is not None:
                if not phone_number:
                    log_event(
                        "intent_shortcut_skipped_no_recipient",
                        correlation_id=correlation_id,
                        intent_label=label,
                    )
                    continue
                if not dispatch_url:
                    log_event("dispatch_wpp_queue_missing", correlation_id=correlation_id, intent_label=label)
                    raise RuntimeError("DISPATCH_WPP_QUEUE_URL empty")
                group_src = phone_number if phone_number else (correlation_id or sqs_message_id)
                dedup_src = f"{correlation_id}-{label}-{sqs_message_id}"
                intent_trace = append_completed_step(
                    msg,
                    step="intent",
                    start=trace_start,
                    metrics={"semantic_router_latency_ms": semantic_router_latency_ms},
                )
                _enqueue_dispatch_wpp_fifo(
                    queue_url=dispatch_url,
                    customer_wa_id=phone_number,
                    business_sender_id=phone_number_id,
                    message=quick,
                    message_group_source=group_src,
                    dedup_source=dedup_src,
                    correlation_id=correlation_id,
                    pipeline_trace=intent_trace,
                )
                log_event(
                    "sqs_dispatch_wpp_sent",
                    correlation_id=correlation_id,
                    intent_label=label,
                )
                continue

            if whatsapp_message_id and phone_number_id:
                typing = send_typing_indicator(
                    message_id=whatsapp_message_id,
                    phone_number_id=phone_number_id,
                )
                log_event(
                    "typing_indicator_prellm",
                    correlation_id=correlation_id,
                    ok=typing.ok,
                    http_status=typing.http_status,
                )
            elif whatsapp_message_id and not phone_number_id:
                log_event(
                    "typing_indicator_skipped_no_business_phone_number_id",
                    correlation_id=correlation_id,
                )
            elif phone_number and not whatsapp_message_id:
                log_event(
                    "typing_indicator_skipped_no_wamid",
                    correlation_id=correlation_id,
                    has_business_phone_number_id=bool(phone_number_id),
                )

            out: dict[str, Any] = {
                "correlation_id": correlation_id,
                "text": text,
                "phone_number": phone_number,
                "phone_number_id": phone_number_id,
                "intent": intent,
                PIPELINE_TRACE_KEY: append_completed_step(
                    msg,
                    step="intent",
                    start=trace_start,
                    metrics={"semantic_router_latency_ms": semantic_router_latency_ms},
                ),
            }
            if not pre_url:
                raise RuntimeError("PRELLM_QUEUE_URL empty")
            _sqs.send_message(QueueUrl=pre_url, MessageBody=json.dumps(out, ensure_ascii=False))
            log_event("sqs_prellm_sent", correlation_id=correlation_id)
        except Exception as e:
            log_event("intent_processor_error", correlation_id=correlation_id, error=str(e))
            if sqs_message_id:
                failures.append({"itemIdentifier": sqs_message_id})

    return {"batchItemFailures": failures}
