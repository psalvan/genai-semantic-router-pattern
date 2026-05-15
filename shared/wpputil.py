"""WhatsApp Cloud API helpers — typing indicator and outbound text (Graph API).

Uses the same SSM layout as the rest of the demo (`ssmutil.ssm_base_path()`).

- **Access token:** env `META_WEBHOOK_WHATSAPP_API_TOKEN` or SSM `meta_webhook_whatsapp_api_token` (SecureString).
- **Business phone number ID** (Graph path segment ``…/{id}/messages`` for the WhatsApp Business line):
  env **`META_PHONE_NUMBER_ID`** (preferred), or `META_WHATSAPP_PHONE_NUMBER_ID`; SSM **`meta_phone_number_id`**
  (preferred), or `meta_whatsapp_phone_number_id`.
- **Graph version:** env `META_WHATSAPP_GRAPH_API_VERSION` (default `v21.0`).

Typing indicator (official): POST `/{phone-number-id}/messages` with `message_id` from the inbound
webhook (`messages[].id`), `status: read`, and `typing_indicator: {type: text}`. This also marks the
message as read.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ssmutil import get_parameter

_DEFAULT_GRAPH_VERSION = "v21.0"
_ENV_ACCESS_TOKEN = "META_WEBHOOK_WHATSAPP_API_TOKEN"
_ENV_PHONE_NUMBER_ID = "META_PHONE_NUMBER_ID"
_ENV_PHONE_NUMBER_ID_LEGACY = "META_WHATSAPP_PHONE_NUMBER_ID"
_ENV_GRAPH_VERSION = "META_WHATSAPP_GRAPH_API_VERSION"
_SSM_ACCESS_TOKEN = "meta_webhook_whatsapp_api_token"
_SSM_PHONE_NUMBER_ID = "meta_phone_number_id"
_SSM_PHONE_NUMBER_ID_LEGACY = "meta_whatsapp_phone_number_id"


@dataclass(frozen=True)
class WhatsAppApiResult:
    """Result of a Graph API POST to `/messages`."""

    http_status: int
    ok: bool
    body: Any
    raw: str


def whatsapp_access_token() -> str:
    """Resolve Meta Graph access token: env first, then SSM `meta_webhook_whatsapp_api_token` (decrypted)."""
    from_env = (os.environ.get(_ENV_ACCESS_TOKEN) or "").strip()
    if from_env:
        return from_env
    return get_parameter(_SSM_ACCESS_TOKEN, decrypt=True).strip()


def whatsapp_phone_number_id() -> str:
    """Resolve WhatsApp Business **phone number ID** (Graph path id), not the customer's MSISDN."""
    for key in (_ENV_PHONE_NUMBER_ID, _ENV_PHONE_NUMBER_ID_LEGACY):
        from_env = (os.environ.get(key) or "").strip()
        if from_env:
            return from_env
    from_ssm = get_parameter(_SSM_PHONE_NUMBER_ID, decrypt=False).strip()
    if from_ssm:
        return from_ssm
    return get_parameter(_SSM_PHONE_NUMBER_ID_LEGACY, decrypt=False).strip()


def whatsapp_graph_api_version() -> str:
    """Graph API version string, e.g. `v21.0` (leading `v` optional in env)."""
    raw = (os.environ.get(_ENV_GRAPH_VERSION) or "").strip()
    if raw:
        return raw if raw.lower().startswith("v") else f"v{raw}"
    from_ssm = get_parameter("meta_whatsapp_graph_api_version", decrypt=False).strip()
    if from_ssm:
        return from_ssm if from_ssm.lower().startswith("v") else f"v{from_ssm}"
    return _DEFAULT_GRAPH_VERSION


def _normalize_customer_wa_id(to: str) -> str:
    """Strip spaces and a leading + for `to` (customer WhatsApp ID / digits)."""
    s = (to or "").strip().replace(" ", "")
    if s.startswith("+"):
        s = s[1:]
    return s


def _messages_url(phone_number_id: str, api_version: str) -> str:
    ver = api_version.strip().lstrip("/")
    pid = phone_number_id.strip()
    return f"https://graph.facebook.com/{ver}/{pid}/messages"


def _graph_post(
    *,
    url: str,
    access_token: str,
    payload: dict[str, Any],
    timeout_s: float,
) -> WhatsAppApiResult:
    """POST JSON to Graph; returns parsed body when response is JSON."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            raw_bytes = resp.read()
            status = getattr(resp, "status", 200) or 200
    except HTTPError as e:
        raw_bytes = e.read() if hasattr(e, "read") else b""
        status = int(e.code)
    except URLError as e:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw=str(e.reason or e))

    raw = raw_bytes.decode("utf-8", errors="replace")
    parsed: Any
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = None
    ok = 200 <= status < 300
    return WhatsAppApiResult(http_status=status, ok=ok, body=parsed, raw=raw)


def send_typing_indicator(
    *,
    message_id: str,
    access_token: str | None = None,
    phone_number_id: str | None = None,
    api_version: str | None = None,
    timeout_s: float = 30.0,
) -> WhatsAppApiResult:
    """Mark inbound message as read and show typing (text) for up to ~25s or until you send a reply.

    `message_id` must be the inbound `wamid` from the webhook (`messages[].id`), per Meta Cloud API.
    """
    mid = (message_id or "").strip()
    if not mid:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="message_id is empty")

    token = (access_token or "").strip() or whatsapp_access_token()
    pid = (phone_number_id or "").strip() or whatsapp_phone_number_id()
    ver = (api_version or "").strip() or whatsapp_graph_api_version()
    if not token:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="access token is empty")
    if not pid:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="phone_number_id is empty")

    url = _messages_url(pid, ver)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": mid,
        "typing_indicator": {"type": "text"},
    }
    return _graph_post(url=url, access_token=token, payload=payload, timeout_s=timeout_s)


def send_message(
    *,
    to: str,
    text: str,
    preview_url: bool = False,
    access_token: str | None = None,
    phone_number_id: str | None = None,
    api_version: str | None = None,
    timeout_s: float = 30.0,
) -> WhatsAppApiResult:
    """Send a **text** outbound message to the customer's WhatsApp (`to` = recipient id / phone digits)."""
    recipient = _normalize_customer_wa_id(to)
    body = (text or "").strip()
    if not recipient:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="to (recipient) is empty")
    if not body:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="text is empty")

    token = (access_token or "").strip() or whatsapp_access_token()
    pid = (phone_number_id or "").strip() or whatsapp_phone_number_id()
    ver = (api_version or "").strip() or whatsapp_graph_api_version()
    if not token:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="access token is empty")
    if not pid:
        return WhatsAppApiResult(http_status=0, ok=False, body=None, raw="phone_number_id is empty")

    url = _messages_url(pid, ver)
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": bool(preview_url), "body": body},
    }
    return _graph_post(url=url, access_token=token, payload=payload, timeout_s=timeout_s)
