#!/usr/bin/env python3
"""Send a message to the ingest SQS queue (same payload shape as the WhatsApp trigger).

The intent-processor expects JSON: correlation_id, text, optional phone_number, optional phone_number_id (WhatsApp Business phone number id), optional whatsapp_message_id (inbound wamid for typing indicator).

Configure the queue URL via INGEST_QUEUE_URL (environment, repo .env, or --queue-url).

Typical URL after deploy: CloudFormation output IngestQueueUrl for stack {ENV}-nvidia-demo.

Requires: pip install boto3 and AWS credentials with sqs:SendMessage on that queue.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a .env-style file into a dict."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def main() -> int:
    """CLI entry: send one simulated WhatsApp-style payload to ingest SQS."""
    parser = argparse.ArgumentParser(
        description="Simulate a WhatsApp message by sending the same JSON to the ingest SQS queue.",
    )
    parser.add_argument(
        "message",
        help="User message text (as if sent from WhatsApp).",
    )
    parser.add_argument(
        "--queue-url",
        "-q",
        default="",
        help="Ingest queue URL (overrides INGEST_QUEUE_URL).",
    )
    parser.add_argument(
        "--correlation-id",
        "-c",
        default="",
        help="Fixed correlation id (default: new UUID).",
    )
    parser.add_argument(
        "--phone-number",
        "-p",
        default="",
        help="Sender phone / WhatsApp ID (optional; propagated through the pipeline).",
    )
    parser.add_argument(
        "--phone-number-id",
        default="",
        help="WhatsApp Business phone_number_id from webhook metadata (optional).",
    )
    parser.add_argument(
        "--whatsapp-message-id",
        default="",
        help="Inbound messages[].id (wamid) for pre-LLM typing indicator (optional).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=REPO_ROOT / ".env",
        help="Path to .env for INGEST_QUEUE_URL / AWS_REGION (default: repo root .env).",
    )
    parser.add_argument(
        "--region",
        "-r",
        default="",
        help="AWS region (default: AWS_REGION or from .env).",
    )
    args = parser.parse_args()

    dotenv = load_dotenv(args.env_file.resolve())
    queue_url = (args.queue_url or os.environ.get("INGEST_QUEUE_URL") or dotenv.get("INGEST_QUEUE_URL") or "").strip()
    if not queue_url:
        print(
            "Set INGEST_QUEUE_URL (environment, .env, or --queue-url). "
            "Example: CloudFormation output IngestQueueUrl.",
            file=sys.stderr,
        )
        return 2

    region = (args.region or os.environ.get("AWS_REGION") or dotenv.get("AWS_REGION") or "").strip()
    correlation_id = (args.correlation_id or "").strip() or str(uuid.uuid4())
    text = args.message.strip()
    phone_number = (args.phone_number or "").strip()
    phone_number_id = (args.phone_number_id or "").strip()
    whatsapp_message_id = (args.whatsapp_message_id or "").strip()
    if not text:
        print("Message text must not be empty.", file=sys.stderr)
        return 2

    payload: dict[str, str] = {
        "correlation_id": correlation_id,
        "text": text,
        "phone_number": phone_number,
        "phone_number_id": phone_number_id,
    }
    if whatsapp_message_id:
        payload["whatsapp_message_id"] = whatsapp_message_id
    body = json.dumps(payload, ensure_ascii=False)

    try:
        import boto3
    except ImportError:
        print("Install boto3: pip install boto3", file=sys.stderr)
        return 2

    client_kwargs: dict[str, str] = {}
    if region:
        client_kwargs["region_name"] = region
    sqs = boto3.client("sqs", **client_kwargs)
    response = sqs.send_message(QueueUrl=queue_url, MessageBody=body)
    sqs_message_id = response.get("MessageId", "")
    print(
        json.dumps(
            {"ok": True, "message_id": sqs_message_id, "correlation_id": correlation_id},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
