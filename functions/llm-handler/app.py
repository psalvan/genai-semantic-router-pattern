"""Lambda: pre-LLM SQS consumer — S3 RAG context, structured LLM output, post-LLM enqueue.

Loads per-intent markdown from S3, calls LiteLLM with a strict JSON schema, records RAG and LLM
timings plus token usage on the ``llm_handler`` ``pipeline_trace`` step, and forwards
``correlation_id``, ``text``, ``intent``, ``llm_output``, phone fields, and trace to **post-LLM** SQS.

Version: 1.0.0
Author: Pablo Salvanha
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

import boto3
import litellm
from litellm import completion
from openai import APIStatusError

from logutil import log_event
from ssmutil import clear_ssm_cache, get_parameter
from traceutil import PIPELINE_TRACE_KEY, append_completed_step, utc_iso

litellm.set_verbose = False
litellm.suppress_debug_info = True

_sqs = boto3.client("sqs")
_s3 = boto3.client("s3")

_AZURE_OPENAI_HOST = "openai.azure.com"
_DEFAULT_AZURE_API_VERSION = "2024-08-01-preview"

_INTENT_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _lambda_code_directory() -> Path:
    """Directory where this function's code and intent `*.json` schemas are deployed (same folder as the handler)."""
    task_root = (os.environ.get("LAMBDA_TASK_ROOT") or "").strip()
    if task_root:
        return Path(task_root)
    return Path(__file__).resolve().parent


def _litellm_call_kwargs(
    api_base: str, model: str, api_key: str, api_version: str, timeout_val: float
) -> dict[str, Any]:
    """Build keyword arguments for litellm.completion (Azure vs other hosts)."""
    base = api_base.strip().rstrip("/")
    kwargs: dict[str, Any] = {
        "model": model.strip(),
        "api_key": api_key,
        "timeout": timeout_val,
    }
    if base:
        kwargs["api_base"] = base
    ver = api_version.strip()
    if _AZURE_OPENAI_HOST in base.lower():
        kwargs["api_version"] = ver or _DEFAULT_AZURE_API_VERSION
    elif ver:
        kwargs["api_version"] = ver
    return kwargs


def _response_for_log(response: Any) -> Any:
    """Normalize an LLM response object for JSON logging."""
    if hasattr(response, "model_dump"):
        try:
            return response.model_dump()
        except Exception:
            pass
    if isinstance(response, dict):
        return response
    return str(response)[:4000]


def _intent_suggested_from_payload(intent: Any) -> str | None:
    """Return intent_suggested from router payload if present and non-empty."""
    if not isinstance(intent, dict):
        return None
    raw = intent.get("intent_suggested")
    if raw is None:
        return None
    label = str(raw).strip()
    return label or None


def _schema_json_path(lambda_code_dir: Path, intent_label: str) -> Path:
    """Per-intent schema: `{intent_label}.json` alongside `app.py` in the Lambda package."""
    return lambda_code_dir / f"{intent_label}.json"


def _parse_schema_document(
    intent_label: str, raw: dict[str, Any]
) -> tuple[str, bool, dict[str, Any]]:
    """Return (response_format name, strict flag, inner JSON Schema object)."""
    inner = raw.get("schema")
    if isinstance(inner, dict) and inner.get("type") == "object":
        name = str(raw.get("name") or f"{intent_label}_output")
        strict = bool(raw.get("strict", True))
        return name, strict, inner
    name = f"{intent_label}_output"
    return name, True, raw


def _load_schema_for_intent(lambda_code_dir: Path, intent_label: str) -> tuple[str, bool, dict[str, Any]] | None:
    """Load and parse `{intent_label}.json` schema bundle, or None if missing/invalid."""
    path = _schema_json_path(lambda_code_dir, intent_label)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _parse_schema_document(intent_label, raw)


def _intent_rag_s3_prefix(rag_prefix: str, intent_label: str) -> str:
    """S3 prefix for this intent: `{rag_prefix}/{intent_label}/` (slashes normalized)."""
    base = rag_prefix.strip().strip("/")
    if base:
        return f"{base}/{intent_label}/"
    return f"{intent_label}/"


def _collect_markdown(bucket: str, prefix: str) -> str:
    """List all objects under `prefix`, keep `.md` keys (case-insensitive), sort alphabetically, concatenate bodies."""
    keys: list[str] = []
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            object_key = obj.get("Key") or ""
            if object_key.lower().endswith(".md"):
                keys.append(object_key)
    keys.sort()
    parts: list[str] = []
    for object_key in keys:
        body = _s3.get_object(Bucket=bucket, Key=object_key)["Body"].read().decode("utf-8", errors="replace")
        parts.append(f"### {object_key}\n{body}")
    return "\n\n".join(parts) if parts else f"(no .md files under s3 prefix {prefix!r})"


def _llm_handler_trace_metrics(response: Any, *, llm_e2e_latency_ms: float, rag_s3_latency_ms: float) -> dict[str, Any]:
    """Build optional metrics dict for the ``llm_handler`` pipeline trace step.

    Reads ``model`` and ``usage`` from the LiteLLM/OpenAI-compatible response (object or dict),
    computes ``llm_tpot_ms`` and ``llm_tps`` when completion token count is positive, and always
    includes ``rag_s3_latency_ms`` and ``llm_e2e_latency_ms``.
    """
    usage_obj = getattr(response, "usage", None)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    if usage_obj is not None:
        pt = getattr(usage_obj, "prompt_tokens", None)
        ct = getattr(usage_obj, "completion_tokens", None)
        if isinstance(usage_obj, dict):
            if pt is None:
                pt = usage_obj.get("prompt_tokens")
            if ct is None:
                ct = usage_obj.get("completion_tokens")
        if isinstance(pt, int):
            prompt_tokens = pt
        elif pt is not None:
            try:
                prompt_tokens = int(pt)
            except (TypeError, ValueError):
                prompt_tokens = None
        if isinstance(ct, int):
            completion_tokens = ct
        elif ct is not None:
            try:
                completion_tokens = int(ct)
            except (TypeError, ValueError):
                completion_tokens = None

    model_raw = getattr(response, "model", None)
    if model_raw is None and isinstance(response, dict):
        model_raw = response.get("model")
    llm_model_id = str(model_raw).strip() if model_raw is not None else None

    llm_tpot_ms: float | None = None
    llm_tps: float | None = None
    if completion_tokens and completion_tokens > 0 and llm_e2e_latency_ms > 0:
        llm_tpot_ms = round(llm_e2e_latency_ms / float(completion_tokens), 6)
        if llm_tpot_ms > 0:
            llm_tps = round(1000.0 / llm_tpot_ms, 6)

    out: dict[str, Any] = {
        "rag_s3_latency_ms": rag_s3_latency_ms,
        "llm_e2e_latency_ms": llm_e2e_latency_ms,
    }
    if llm_model_id:
        out["llm_model_id"] = llm_model_id
    if prompt_tokens is not None:
        out["llm_input_tokens"] = prompt_tokens
    if completion_tokens is not None:
        out["llm_output_tokens"] = completion_tokens
    if llm_tpot_ms is not None:
        out["llm_tpot_ms"] = llm_tpot_ms
    if llm_tps is not None:
        out["llm_tps"] = llm_tps
    return out


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process each pre-LLM SQS record: RAG + schema + LLM, then enqueue post-LLM or partial failure."""
    del context
    clear_ssm_cache()
    failures: list[dict[str, str]] = []
    post_url = (os.environ.get("POSTLLM_QUEUE_URL") or "").strip()
    bucket = (os.environ.get("RAG_BUCKET_NAME") or "").strip()
    rag_prefix = (os.environ.get("RAG_S3_PREFIX") or "context/").strip()
    lambda_code_dir = _lambda_code_directory()

    for record in event.get("Records") or []:
        sqs_message_id = record.get("messageId") or ""
        correlation_id: str | None = None
        try:
            msg = json.loads(record.get("body") or "{}")
            correlation_id = (msg.get("correlation_id") or "").strip() or None
            text = (msg.get("text") or "").strip()
            phone_number = (msg.get("phone_number") or "").strip()
            phone_number_id = (msg.get("phone_number_id") or "").strip()
            intent = msg.get("intent")
            log_event(
                "sqs_prellm_received",
                correlation_id=correlation_id,
                text_len=len(text),
                has_phone=bool(phone_number),
                has_phone_number_id=bool(phone_number_id),
            )
            if not correlation_id or not text:
                log_event("invalid_prellm_payload", correlation_id=correlation_id)
                continue

            trace_start = utc_iso()
            intent_label = _intent_suggested_from_payload(intent)
            if not intent_label or not _INTENT_LABEL_PATTERN.fullmatch(intent_label):
                log_event(
                    "intent_not_mapped",
                    correlation_id=correlation_id,
                    reason="missing_or_invalid_intent_label",
                    lambda_code_dir=str(lambda_code_dir),
                    intent_payload=intent if isinstance(intent, dict) else type(intent).__name__,
                )
                continue

            schema_bundle = _load_schema_for_intent(lambda_code_dir, intent_label)
            if schema_bundle is None:
                log_event(
                    "intent_not_mapped",
                    correlation_id=correlation_id,
                    reason="no_schema_json_for_intent",
                    intent_label=intent_label,
                    lambda_code_dir=str(lambda_code_dir),
                    expected_schema_path=str(_schema_json_path(lambda_code_dir, intent_label)),
                )
                continue

            schema_name, schema_strict, schema = schema_bundle

            if not bucket:
                raise RuntimeError("RAG_BUCKET_NAME empty")

            intent_s3_prefix = _intent_rag_s3_prefix(rag_prefix, intent_label)
            _t_rag0 = time.perf_counter()
            md_blob = _collect_markdown(bucket, intent_s3_prefix)
            rag_s3_latency_ms = round((time.perf_counter() - _t_rag0) * 1000.0, 3)
            log_event(
                "s3_context_loaded",
                correlation_id=correlation_id,
                intent_label=intent_label,
                s3_prefix=intent_s3_prefix,
                md_chars=len(md_blob),
            )

            api_base = get_parameter("main_llm_api").strip()
            model = get_parameter("main_llm_model").strip()
            api_key = get_parameter("main_llm_key", decrypt=True).strip()
            timeout_s = get_parameter("main_llm_timeout").strip()
            api_version = get_parameter("main_llm_api_version").strip()
            try:
                timeout_val = float(timeout_s) if timeout_s else 120.0
            except ValueError:
                timeout_val = 120.0

            if not api_base or not model or not api_key:
                log_event("llm_ssm_missing", correlation_id=correlation_id, has_api=bool(api_base), has_model=bool(model), has_key=bool(api_key))
                raise RuntimeError("main_llm SSM incomplete")

            call_kw = _litellm_call_kwargs(api_base, model, api_key, api_version, timeout_val)
            system_content = (
                "You are an assistant in a demo pipeline. Follow the API JSON schema exactly for your answer.\n"
                f"RAG context (all .md objects under S3 prefix {intent_s3_prefix!r}, alphabetical order by key):\n"
                f"{md_blob}\n\n"
                "Intent / metadata from the semantic-router (JSON):\n"
                f"{json.dumps(intent, ensure_ascii=False)}"
            )
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": text},
            ]
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": schema_strict,
                    "schema": schema,
                },
            }
            request_log: dict[str, Any] = {
                "model": call_kw["model"],
                "messages": messages,
                "response_format": response_format,
            }
            log_event(
                "llm_request",
                correlation_id=correlation_id,
                model=call_kw["model"],
                intent_label=intent_label,
                schema_name=schema_name,
                schema_path=str(_schema_json_path(lambda_code_dir, intent_label)),
                timeout_s=timeout_val,
                api_base=call_kw.get("api_base"),
                api_version=call_kw.get("api_version"),
                request_payload=request_log,
            )
            try:
                _t_llm0 = time.perf_counter()
                response = completion(
                    messages=messages,
                    response_format=response_format,
                    **call_kw,
                )
                llm_e2e_latency_ms = round((time.perf_counter() - _t_llm0) * 1000.0, 3)
            except APIStatusError as e:
                err_body: Any = None
                preview = str(e)
                if e.response is not None:
                    try:
                        err_body = e.response.json()
                    except json.JSONDecodeError:
                        err_body = e.response.text
                    preview = (e.response.text or preview)[:500]
                log_event(
                    "llm_http_error",
                    correlation_id=correlation_id,
                    status=e.status_code,
                    response_payload=err_body,
                    body_preview=preview,
                )
                raise

            log_event("llm_response", correlation_id=correlation_id, response_payload=_response_for_log(response))
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise RuntimeError("empty choices from LLM")
            msg_out = choices[0].message
            content = getattr(msg_out, "content", None)
            if isinstance(content, str):
                try:
                    llm_obj = json.loads(content)
                except json.JSONDecodeError:
                    log_event("llm_parse_error", correlation_id=correlation_id, raw_preview=content[:300])
                    raise
            elif isinstance(content, dict):
                llm_obj = content
            else:
                log_event("llm_parse_error", correlation_id=correlation_id, raw_type=str(type(content)))
                raise RuntimeError("unexpected message.content")

            llm_trace_metrics = _llm_handler_trace_metrics(
                response,
                llm_e2e_latency_ms=llm_e2e_latency_ms,
                rag_s3_latency_ms=rag_s3_latency_ms,
            )
            out: dict[str, Any] = {
                "correlation_id": correlation_id,
                "text": text,
                "phone_number": phone_number,
                "phone_number_id": phone_number_id,
                "intent": intent,
                "llm_output": llm_obj,
                PIPELINE_TRACE_KEY: append_completed_step(
                    msg,
                    step="llm_handler",
                    start=trace_start,
                    metrics=llm_trace_metrics,
                ),
            }
            if not post_url:
                raise RuntimeError("POSTLLM_QUEUE_URL empty")
            _sqs.send_message(QueueUrl=post_url, MessageBody=json.dumps(out, ensure_ascii=False))
            log_event("sqs_postllm_sent", correlation_id=correlation_id)
        except Exception as e:
            log_event("llm_handler_error", correlation_id=correlation_id, error=str(e))
            if sqs_message_id:
                failures.append({"itemIdentifier": sqs_message_id})

    return {"batchItemFailures": failures}
