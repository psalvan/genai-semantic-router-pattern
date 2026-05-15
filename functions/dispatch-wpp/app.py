"""Lambda: FIFO **dispatch-wpp** queue consumer — sends outbound WhatsApp text via Graph API.

Uses :func:`wpputil.send_message` with credentials from env or SSM (same layout as the rest of the
stack). On success, appends a ``dispatch_wpp`` entry to ``pipeline_trace`` for structured logs.

**Minimum SQS message body (JSON object):**

- **phone_number_id** — recipient: the customer's WhatsApp ID (digits / wa_id; maps to Graph ``to``).
  Do not confuse with the Business ``metadata.phone_number_id`` from the inbound webhook.
- **message** — plain text to send.

**Optional fields:**

- **sender_phone_number_id** — Business phone number id for the Graph URL path; if omitted, uses
  ``META_PHONE_NUMBER_ID`` / SSM defaults.
- **correlation_id** — propagated for log correlation.
- **pipeline_trace** — prior steps; this handler appends ``dispatch_wpp``.

**FIFO:** publishers must set ``MessageGroupId`` and ``MessageDeduplicationId`` (``ContentBasedDeduplication`` is ``false`` on this queue).

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import json
from typing import Any

from logutil import log_event
from ssmutil import clear_ssm_cache
from traceutil import append_completed_step, utc_iso
from wpputil import send_message


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process each FIFO record: validate body, call Graph send, extend ``pipeline_trace``, log."""
    del context
    clear_ssm_cache()
    failures: list[dict[str, str]] = []

    for record in event.get("Records") or []:
        sqs_message_id = record.get("messageId") or ""
        try:
            raw = record.get("body") or "{}"
            msg = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(msg, dict):
                raise ValueError("body must be a JSON object")

            dest = (msg.get("phone_number_id") or msg.get("to") or "").strip()
            text = (msg.get("message") or "").strip()
            sender_pid = (msg.get("sender_phone_number_id") or "").strip() or None

            if not dest or not text:
                raise ValueError("phone_number_id (destination) and message are required")

            trace_start = utc_iso()
            log_event(
                "dispatch_wpp_sending",
                message_id=sqs_message_id,
                dest_len=len(dest),
                text_len=len(text),
                has_sender_override=bool(sender_pid),
            )
            result = send_message(
                to=dest,
                text=text,
                phone_number_id=sender_pid,
            )
            if not result.ok:
                log_event(
                    "dispatch_wpp_graph_error",
                    message_id=sqs_message_id,
                    http_status=result.http_status,
                    body=result.body,
                    raw_head=(result.raw or "")[:500],
                )
                raise RuntimeError(f"Graph API error http={result.http_status}")

            pipeline_trace = append_completed_step(msg, step="dispatch_wpp", start=trace_start)
            log_event(
                "dispatch_wpp_sent",
                message_id=sqs_message_id,
                http_status=result.http_status,
                correlation_id=(msg.get("correlation_id") or "").strip() or None,
                pipeline_trace=pipeline_trace,
            )
        except Exception as e:
            log_event("dispatch_wpp_error", message_id=sqs_message_id, error=str(e))
            if sqs_message_id:
                failures.append({"itemIdentifier": sqs_message_id})

    return {"batchItemFailures": failures}
