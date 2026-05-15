"""Lambda Function URL: Meta WhatsApp webhook verification (GET) and inbound message fan-out to SQS.

POST handlers extract text and optional WhatsApp metadata, assign ``correlation_id``, seed
``pipeline_trace`` with a ``webhook`` step, and enqueue a JSON payload to the **ingest** queue.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any

import boto3

from logutil import log_event
from ssmutil import clear_ssm_cache, get_parameter
from traceutil import PIPELINE_TRACE_KEY, append_completed_step, utc_iso

_sqs = boto3.client("sqs")


def _meta_verify_token() -> str:
    """Resolve webhook verify token from env or SSM."""
    from_env = (os.environ.get("META_WEBHOOK_VERIFY_TOKEN") or os.environ.get("META_VERIFY_TOKEN") or "").strip()
    if from_env:
        return from_env
    return get_parameter("meta_webhook_verify_token", decrypt=True).strip()


def _response(status: int, body: str, content_type: str = "application/json") -> dict[str, Any]:
    """Build an API Gateway v2 HTTP response dict."""
    return {
        "statusCode": status,
        "headers": {"content-type": content_type},
        "body": body,
    }


def _extract_texts_phone_and_number_id(payload: dict[str, Any]) -> tuple[list[str], str, str, str]:
    """Collect text bodies, sender `from`, business `metadata.phone_number_id`, and last text `messages[].id`."""
    texts: list[str] = []
    phone_number = ""
    phone_number_id = ""
    wa_message_id = ""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if not phone_number_id:
                meta = value.get("metadata") or {}
                raw_id = meta.get("phone_number_id")
                if raw_id is not None and str(raw_id).strip():
                    phone_number_id = str(raw_id).strip()
            for msg in value.get("messages") or []:
                if msg.get("type") != "text":
                    continue
                body = (msg.get("text") or {}).get("body")
                if isinstance(body, str) and body.strip():
                    texts.append(body.strip())
                    if not phone_number:
                        phone_number = str(msg.get("from") or "").strip()
                    mid = str(msg.get("id") or "").strip()
                    if mid:
                        wa_message_id = mid
    return texts, phone_number, phone_number_id, wa_message_id


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle GET verification or POST webhook; enqueue extracted text to ingest SQS."""
    del context
    clear_ssm_cache()
    req = event.get("requestContext") or {}
    http = req.get("http") or {}
    method = (http.get("method") or "GET").upper()

    if method == "GET":
        qs = event.get("queryStringParameters") or {}
        mode = (qs.get("hub.mode") or "").strip()
        token = (qs.get("hub.verify_token") or "").strip()
        challenge = qs.get("hub.challenge") or ""
        expected = _meta_verify_token()
        log_event("webhook_get", mode=mode, has_challenge=bool(challenge))
        if mode == "subscribe" and challenge:
            if expected and token != expected:
                log_event("webhook_verify_failed", extra={"reason": "token_mismatch"})
                return _response(403, '{"error":"verify_token"}')
            log_event("webhook_verify_ok", extra={"challenge_len": len(challenge)})
            return _response(200, challenge, content_type="text/plain")
        return _response(200, '{"ok":true}')

    correlation_id = str(uuid.uuid4())
    log_event("webhook_received", correlation_id=correlation_id, method=method)

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            log_event("body_decode_error", correlation_id=correlation_id)
            return _response(400, '{"error":"invalid_body"}')

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        log_event("json_parse_error", correlation_id=correlation_id)
        return _response(400, '{"error":"invalid_json"}')

    texts, phone_number, phone_number_id, wa_message_id = _extract_texts_phone_and_number_id(payload)
    if not texts:
        log_event("no_text_messages", correlation_id=correlation_id)
        return _response(200, '{"ok":true,"ingested":false}')

    text = "\n".join(texts)
    trace_start = utc_iso()
    log_event(
        "text_extracted",
        correlation_id=correlation_id,
        text_len=len(text),
        segments=len(texts),
        has_phone=bool(phone_number),
        has_phone_number_id=bool(phone_number_id),
    )

    queue_url = (os.environ.get("INGEST_QUEUE_URL") or "").strip()
    if not queue_url:
        log_event("misconfig_no_queue", correlation_id=correlation_id)
        return _response(500, '{"error":"config"}')

    ingest: dict[str, Any] = {
        "correlation_id": correlation_id,
        "text": text,
        "phone_number": phone_number,
        "phone_number_id": phone_number_id,
    }
    if wa_message_id:
        ingest["whatsapp_message_id"] = wa_message_id
    ingest[PIPELINE_TRACE_KEY] = append_completed_step({}, step="webhook", start=trace_start)
    body = json.dumps(ingest, ensure_ascii=False)
    _sqs.send_message(QueueUrl=queue_url, MessageBody=body)
    log_event("sqs_ingest_sent", correlation_id=correlation_id, queue_hint=queue_url.split("/")[-1])
    return _response(200, json.dumps({"ok": True, "ingested": True, "correlation_id": correlation_id}))
