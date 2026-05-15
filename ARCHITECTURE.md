# Study Guide — WhatsApp → Semantic Router → LLM Pipeline

This document traces the **end-to-end flow** of the FSI GenAI demo: from a WhatsApp user message through API Gateway, Lambdas, SQS (including the **dispatch-wpp** FIFO queue), the semantic-router service, dynamic RAG, structured LLM output, post-processing, and **outbound** delivery back to WhatsApp. It names the **main modules, functions, environment variables, and SSM parameters** you need to reason about the system—not a line-by-line walkthrough.

---

## Cross-cutting building blocks

| Concern | Where it lives | Role |
|--------|----------------|------|
| **SSM path prefix** | `shared/ssmutil.py` — `ssm_base_path()` | Resolves to `/{ENVIRONMENT}/nvidia-demo` (e.g. `/dev/nvidia-demo`). `ENVIRONMENT` is set globally for all Lambdas in `template.yaml` (`Globals.Function.Environment`). |
| **Parameter fetch** | `shared/ssmutil.py` — `get_parameter(relative_name, decrypt=False)` | Builds full name `/{env}/nvidia-demo/{relative_name}`, caches per invocation; `clear_ssm_cache()` runs at the start of each `lambda_handler` so fresh reads happen every invocation. |
| **Structured logs** | `shared/logutil.py` — `log_event(phase, correlation_id=..., **fields)` | One JSON line per event to stdout (CloudWatch Insights friendly); always correlate with `correlation_id` when present. |
| **Pipeline trace** | `shared/traceutil.py` — `PIPELINE_TRACE_KEY`, `get_pipeline_trace`, `append_completed_step`, `utc_iso`, `step_duration_ms` | Single JSON array `pipeline_trace` on SQS payloads: each step is a dict with `step`, `start`, `end` (UTC ISO-8601) plus optional numeric metrics merged by `append_completed_step(..., metrics={...})`. |
| **WhatsApp Graph helpers** | `shared/wpputil.py` — `send_message`, `send_typing_indicator` | Outbound text and typing indicator via WhatsApp Cloud API; token and business `phone_number_id` from env or SSM (see Phase 8). |
| **IaC & wiring** | `template.yaml` | HTTP API, **five** Lambdas, **four** primary SQS queues (ingest, pre-LLM, post-LLM, **dispatch-wpp FIFO**) + DLQs, RAG bucket, optional EC2 semantic-router, SSM parameters. |
| **Lambda packaging** | `Makefile` targets `build-*` | Copies `shared/` into each artifact dir, then the function’s `app.py`; **LlmHandler** also copies `functions/llm-handler/*.json` (per-intent JSON schemas). |

---

## Phase 1 — User input on WhatsApp

- The **WhatsApp Cloud API** delivers user messages to your configured **callback URL** (HTTPS). Only **text** messages are ingested by this codebase’s webhook logic; other message types are ignored at extraction time.
- Meta’s webhook contract uses JSON with `entry[] → changes[] → value.messages[]` for inbound payloads. You do not implement the WhatsApp client here; you only host the HTTPS endpoint the Meta app calls.

**Relevant docs in-repo:** `README.md` (architecture overview), Meta’s WhatsApp webhook documentation (external).

---

## Phase 2 — API Gateway receives Meta’s payload

- **Resource:** `DemoHttpApi` (`AWS::Serverless::HttpApi`) in `template.yaml`.
- **Route:** `ANY` on path `/` is bound to `WhatsappWebhookTrigger` (`Events.HttpAny`).
- **Payload shape:** API Gateway HTTP API v2 passes a Lambda proxy event with `requestContext.http.method`, optional `queryStringParameters`, `body`, and `isBase64Encoded`.

**Output to remember:** After deploy, stack output `HttpApiUrl` is the base URL for the webhook (Meta must call this URL for GET verification and POST notifications).

---

## Phase 3 — Webhook Lambda (`WhatsappWebhookTrigger`)

**Code:** `functions/whatsapp-trigger/app.py`  
**Handler:** `lambda_handler`

### Responsibilities

1. **GET — Meta subscription verification**  
   - Reads `hub.mode`, `hub.verify_token`, `hub.challenge` from query string.  
   - Compares token to `_meta_verify_token()`:
     - **Env first:** `META_WEBHOOK_VERIFY_TOKEN` or `META_VERIFY_TOKEN`.  
     - **Else SSM:** `get_parameter("meta_webhook_verify_token", decrypt=True)` → full path `/{env}/nvidia-demo/meta_webhook_verify_token`.  
   - On success, returns HTTP 200 with `hub.challenge` as **plain text** (Meta expects the raw challenge string).

2. **POST — Inbound webhook**  
   - Generates **`correlation_id`** = `str(uuid.uuid4())` (one per HTTP request, not per WhatsApp message id).  
   - Decodes body if `isBase64Encoded`, then `json.loads` into a dict.  
   - **`_extract_texts_phone_and_number_id(payload)`** walks `entry → changes → value.messages`, keeps only `type == "text"`, collects `text.body` strings, strips empties; also captures **`from`** (customer wa_id), **`metadata.phone_number_id`** (Business line id for Graph URLs), and the last text message’s **`messages[].id`** (`whatsapp_message_id`) for typing indicators downstream.  
   - Joins text segments with `"\n"`. If no text: returns 200 with `ingested: false` (no SQS send).  
   - Builds ingest payload: `correlation_id`, `text`, optional `phone_number`, `phone_number_id`, optional `whatsapp_message_id`, and **`pipeline_trace`** = `append_completed_step({}, step="webhook", start=trace_start)` (first completed step).  
   - Sends to SQS via `boto3` SQS client (`INGEST_QUEUE_URL`).

### Environment (from template)

| Variable | Meaning |
|----------|---------|
| `INGEST_QUEUE_URL` | URL of the **ingest** queue (`IngestQueue`). |

### Key functions

| Function | Purpose |
|----------|---------|
| `_meta_verify_token()` | Resolve verify token from env or SSM. |
| `_extract_texts_phone_and_number_id(payload)` | Meta JSON → texts, customer phone, business `phone_number_id`, last text `wamid`. |
| `_response(...)` | Build API Gateway HTTP API response dict. |
| `lambda_handler` | GET verify vs POST ingest branch. |

---

## Phase 4 — First SQS fan-out (“ingest” queue)

- **Queue:** `IngestQueue` — physical name `{Environment}-nvidia-demo-ingest` (see `template.yaml`).  
- **Consumer:** `IntentProcessor` Lambda (SQS event source, `BatchSize: 1`, partial batch failure reporting).

**Message body contract (JSON):**

```json
{
  "correlation_id": "<uuid>",
  "text": "<user text>",
  "phone_number": "<customer wa_id, optional>",
  "phone_number_id": "<business phone_number_id, optional>",
  "whatsapp_message_id": "<inbound text message id, optional>",
  "pipeline_trace": [ { "step": "webhook", "start": "<iso>", "end": "<iso>" } ]
}
```

This is the **decoupling buffer** between the fast webhook and slower intent routing. A companion **DLQ** (`IngestDlq`) receives messages that exceed `maxReceiveCount`.

---

## Phase 5 — Intent Lambda + semantic-router + pre-LLM queue

**Code:** `functions/intent-processor/app.py`  
**Handler:** `lambda_handler`

### Flow

1. For each SQS record: `json.loads` body → `correlation_id`, `text`, optional `phone_number`, `phone_number_id`, `whatsapp_message_id`, existing **`pipeline_trace`**; skip invalid payloads without `correlation_id`/`text` (logs `invalid_ingest_payload`).
2. **Router HTTP call** (sync, `httpx.Client(timeout=60.0)`):
   - **URL:** `get_parameter("SMART_ROUTER_URL")` → `/{env}/nvidia-demo/SMART_ROUTER_URL` (stack may set this to `http://<EIP>:8000/check-intent` when EC2 router is enabled).
   - **Auth:** `get_parameter("SMART_ROUTER_KEY", decrypt=True)`; if non-empty, sends `Authorization: Bearer <key>`.
   - **Body:** `{"text": "<user text>"}`.
   - **Latency:** wall time of `client.post(...)` only is recorded as **`semantic_router_latency_ms`** (ms, rounded).
3. **Response JSON** is treated as the **`intent`** object (the whole router JSON is forwarded).
4. **Branch on `intent_suggested`:**
   - **Shortcut (`ChitChat`, `Unknown`):** if `phone_number` is present and `DISPATCH_WPP_QUEUE_URL` is configured, appends trace step `intent` with `metrics={"semantic_router_latency_ms": ...}`, then enqueues a **fixed template reply** on **`DispatchWppQueue` (FIFO)** via `_enqueue_dispatch_wpp_fifo` (payload includes `phone_number_id` as destination `phone_number_id`, business id as `sender_phone_number_id`, optional `correlation_id`, **`pipeline_trace`**). **No** pre-LLM / LLM / post-LLM for this path. If `phone_number` is missing, logs and skips (`intent_shortcut_skipped_no_recipient`).
   - **All other intents:** if `whatsapp_message_id` and `phone_number_id` are present, calls **`send_typing_indicator`** (`shared/wpputil.py`) before pre-LLM; then enqueues **pre-LLM** with `correlation_id`, `text`, `phone_number`, `phone_number_id`, `intent`, and **`pipeline_trace`** extended with step `intent` (same `semantic_router_latency_ms` metric).

### SSM parameters used here

| Relative name (under `/{env}/nvidia-demo/`) | Typical type | Used for |
|-----------------------------------------------|--------------|----------|
| `SMART_ROUTER_URL` | String | POST target for intent check. |
| `SMART_ROUTER_KEY` | SecureString (optional) | Bearer token; EC2 service can sync this from SSM. |

### semantic-router service (HTTP API)

**Code:** `services/semantic-router/main.py`  
**Endpoint:** `POST /check-intent`  
**Auth:** `verify_token` dependency — if `SMART_ROUTER_KEY` env is set on the service, requires matching `Bearer`; if unset, allows anonymous (dev only).

| Symbol | Role |
|--------|------|
| `IntentCheckRequest` | Pydantic body: `{ "text": str }`. |
| `check_intent` | Encodes user text with `SentenceTransformer("all-MiniLM-L6-v2")`, cosine-sim vs pre-encoded `INTENT_ANCHOR_PHRASES`, threshold `TOPIC_SCORE_THRESHOLD` (0.60). |
| **Response keys** | `intent_suggested` (label string, e.g. `Pix_Transfer_Balance`, `FAQ_Policy`, `Fraud_Alert_Loss`, `ChitChat`, or `Unknown`), `confidence` (float). |

**Important:** `LlmHandler` keys off **`intent_suggested`** as a filesystem-safe label; it must match `^[A-Za-z0-9_-]+$` and have a matching `{intent_suggested}.json` schema file in the Lambda package (see Phase 6). Messages that reach pre-LLM but lack a schema file are dropped with structured logs (`intent_not_mapped`). **`ChitChat` / `Unknown`** are handled in **IntentProcessor** and do not enqueue pre-LLM.

### Environment (IntentProcessor)

| Variable | Meaning |
|----------|---------|
| `PRELLM_QUEUE_URL` | `PreLlmQueue` — `{Environment}-nvidia-demo-prellm`. |
| `DISPATCH_WPP_QUEUE_URL` | `DispatchWppQueue` (FIFO) — required for shortcut replies and used by post-LLM outbound. |

### Error handling

- Exceptions → `batchItemFailures` with `itemIdentifier` = SQS `messageId` for retry / DLQ policy.

### Spec-Driven Design — intents, schema e RAG

O contrato entre **semantic-router** (`intent_suggested`), **LlmHandler** (JSON Schema para `response_format`) e **S3** (prefixo `rag-context/<IntentLabel>/` espelhado em `context/<IntentLabel>/`) é **spec-driven**: cada intent operacional precisa de artefatos alinhados no repositório.

| Intent (`intent_suggested`) | Schema (pacote Lambda) | Contexto RAG (local → S3) |
|-----------------------------|-------------------------|---------------------------|
| `Pix_Transfer_Balance` | [`functions/llm-handler/Pix_Transfer_Balance.json`](functions/llm-handler/Pix_Transfer_Balance.json) | [`rag-context/Pix_Transfer_Balance/`](rag-context/Pix_Transfer_Balance/) |
| `Fraud_Alert_Loss` | [`functions/llm-handler/Fraud_Alert_Loss.json`](functions/llm-handler/Fraud_Alert_Loss.json) | [`rag-context/Fraud_Alert_Loss/`](rag-context/Fraud_Alert_Loss/) |
| `FAQ_Policy` | [`functions/llm-handler/FAQ_Policy.json`](functions/llm-handler/FAQ_Policy.json) | [`rag-context/FAQ_Policy/`](rag-context/FAQ_Policy/) |

Mensagens já enfileiradas em **pre-LLM** cujo `intent_suggested` **não** possui `{IntentLabel}.json` correspondente no artefato são **descartadas de forma controlada** (logs estruturados, sem retry infinito para esse caminho), o que **força um contrato rígido** entre roteamento semântico e geração estruturada.

---

## Phase 6 — LLM Handler: dynamic context + JSON schema → post-LLM queue

**Code:** `functions/llm-handler/app.py`  
**Handler:** `lambda_handler`  
**Dependencies:** `litellm.completion`, `boto3` (SQS, S3), OpenAI-compatible APIs (Azure supported via `api_base` host detection).

### Input message (from `PreLlmQueue`)

JSON with `correlation_id`, `text`, **`intent`** (full router response), optional **`phone_number`**, **`phone_number_id`**, and **`pipeline_trace`** (list from prior stages).

### Step A — Resolve intent label

- **`_intent_suggested_from_payload(intent)`** reads `intent["intent_suggested"]`.  
- **`_INTENT_LABEL_PATTERN`** must match (alphanumeric, `_`, `-` only).  
- If invalid → log and **continue** (message dropped from processing without throwing—no DLQ retry for that path).

### Step B — Load JSON Schema (structured output)

- **`_lambda_code_directory()`** — deployment dir (`LAMBDA_TASK_ROOT` or `__file__` parent).  
- **`_schema_json_path(dir, intent_label)`** → `{intent_label}.json`.  
- **`_load_schema_for_intent`** → **`_parse_schema_document`**: supports either a wrapper `{"name", "strict", "schema": { "type": "object", ... }}` or a raw dict (treated as inner schema with default name `{intent_label}_output`).

**Repo artifacts:** `functions/llm-handler/*.json` (e.g. `Pix_Transfer_Balance.json`, `FAQ_Policy.json`, `Fraud_Alert_Loss.json`) — copied at build time by `Makefile` `build-LlmHandler`.

### Step C — Load RAG markdown from S3

- **Bucket:** env `RAG_BUCKET_NAME` → `RagContextBucket` in template.  
- **Prefix:** `RAG_S3_PREFIX` (default in template `context/`) + `/{intent_label}/` via **`_intent_rag_s3_prefix`**.  
- **`_collect_markdown`**: lists objects under prefix, keeps keys ending in `.md` (case-insensitive), sorts keys, concatenates with `### <key>` headers.  
- **`rag_s3_latency_ms`:** wall time around the whole list+get S3 phase (`time.perf_counter()`), stored on the `llm_handler` trace step.

**Local source mirrored to S3:** `rag-context/<IntentFolder>/*.md` (see `make sync-rag-context` in `Makefile`).

### Step D — Call the main LLM (LiteLLM)

**SSM parameters** (all under `/{env}/nvidia-demo/`):

| Relative name | Role |
|---------------|------|
| `main_llm_api` | Base URL (OpenAI-compatible / Azure). |
| `main_llm_model` | Model id. |
| `main_llm_key` | API key (decrypt). |
| `main_llm_timeout` | Seconds as string; invalid → 120.0 default. |
| `main_llm_api_version` | If host contains `openai.azure.com`, passed as Azure API version (default `2024-08-01-preview` if empty); otherwise optional. |

**Call path:** **`_litellm_call_kwargs`** builds kwargs for **`completion(...)`** with `response_format` = OpenAI-style **`json_schema`** (name, strict, inner schema from Step B). **`llm_e2e_latency_ms`** wraps only the `completion(...)` call.

**Prompt structure:**

- **System:** Demo instructions + full **RAG blob** + serialized **`intent`** JSON from the router.  
- **User:** inbound `text` from the queue message.

**Trace metrics (`_llm_handler_trace_metrics`):** after a successful completion, merges into the `llm_handler` step (alongside `rag_s3_latency_ms` / `llm_e2e_latency_ms`): optional **`llm_model_id`**, **`llm_input_tokens`** / **`llm_output_tokens`** from `response.usage`, and derived **`llm_tpot_ms`** / **`llm_tps`** when output token count is positive.

### Step E — Parse LLM output and enqueue post-LLM

- Reads `choices[0].message.content` (string JSON or dict).  
- **`llm_obj`** = parsed JSON object.  
- **`pipeline_trace`:** `append_completed_step(msg, step="llm_handler", start=trace_start, metrics=llm_trace_metrics)` preserves prior steps and appends the LLM stage with timestamps + metrics.  
- **Outbound body:** `correlation_id`, `text`, `intent`, `llm_output`, optional `phone_number`, `phone_number_id`, **`pipeline_trace`** → **`POSTLLM_QUEUE_URL`** (`PostLlmQueue`).

### Environment (LlmHandler)

| Variable | Meaning |
|----------|---------|
| `POSTLLM_QUEUE_URL` | Post-LLM / “post-processing” queue. |
| `RAG_BUCKET_NAME` | S3 bucket for markdown context. |
| `RAG_S3_PREFIX` | Prefix before `/{intent_label}/`. |

### Key helper summary

| Function | Purpose |
|----------|---------|
| `_intent_suggested_from_payload` | Extract `intent_suggested` string. |
| `_load_schema_for_intent` / `_parse_schema_document` | Load `{intent}.json` for `response_format`. |
| `_intent_rag_s3_prefix` / `_collect_markdown` | Dynamic RAG context for the intent. |
| `_llm_handler_trace_metrics` | Build dict of RAG/LLM timings, model id, tokens, TPOT/TPS for `pipeline_trace`. |
| `_litellm_call_kwargs` | Azure vs non-Azure completion kwargs. |
| `_response_for_log` | Safe logging of LLM response objects. |

---

## Phase 7 — Post-processing (`PostLlmConsumer`)

**Code:** `functions/post-llm/app.py`  
**Handler:** `lambda_handler`

- Triggered by **`PostLlmQueue`** (`{Environment}-nvidia-demo-postllm`).  
- Parses each record’s JSON body; logs **`post_llm_received`** with `correlation_id` and `messageId`.  
- Prints the **full message** as one JSON line to stdout (`print(line)`) for audit / replay.  
- Reads structured **`llm_output`** and **`next_action`** (`NEED_MORE_INFO`, `PROVIDE_DIRECT_ANSWER`, `EXECUTE_TRANSACTION`, or empty → user text path). **`EXECUTE_TRANSACTION`** maps to a fixed “please wait” outbound string.  
- Derives WhatsApp **outbound text** from `llm_output.user_response` when applicable.  
- Appends trace step **`post_llm`** via `append_completed_step`.  
- If there is outbound text and **`phone_number`** (customer wa_id), sends a message to **`DISPATCH_WPP_QUEUE_URL`** (FIFO): the enqueue helper maps the customer wa_id to the dispatch JSON field **`phone_number_id`** (Graph recipient `to`), passes optional **`sender_phone_number_id`** from the inbound business line id, `message`, `correlation_id`, and updated **`pipeline_trace`**. Logs distinguish enqueue vs skip (missing queue URL, missing recipient, empty message).  
- **`print_performance_report(post_trace)`** — after successful handling, logs a multi-line **`INFO`** block to CloudWatch summarizing ingest/routing prep, semantic router latency, RAG S3 time, LLM inference (bottleneck % of total wall time), TPOT/TPS/tokens, and post-LLM duration, using **`step_duration_ms`** and metrics embedded in `pipeline_trace`. **Shortcut flows** that never hit post-LLM do not produce this report.

Partial batch failures are supported the same way as the other SQS Lambdas.

### Environment (PostLlmConsumer)

| Variable | Meaning |
|----------|---------|
| `DISPATCH_WPP_QUEUE_URL` | FIFO queue consumed by **DispatchWpp** (`{Environment}-nvidia-demo-dispatch-wpp.fifo`). |

---

## Phase 8 — Outbound WhatsApp (`DispatchWpp`)

**Code:** `functions/dispatch-wpp/app.py`  
**Handler:** `lambda_handler`

- Triggered by **`DispatchWppQueue`** (FIFO, with **`DispatchWppDlq`**).  
- Payload JSON: required **`phone_number_id`** (destination customer Graph id / `to`), **`message`** (text); optional **`sender_phone_number_id`** (Business id for URL path; else SSM/env default), **`correlation_id`**, **`pipeline_trace`**.  
- Calls **`send_message`** from `shared/wpputil.py` (Graph `POST /{phone-number-id}/messages`).  
- On success, appends step **`dispatch_wpp`** with `append_completed_step` and logs **`dispatch_wpp_sent`** including **`pipeline_trace`**.

**SSM / env for Graph (see `wpputil.py` docstring):** access token `META_WEBHOOK_WHATSAPP_API_TOKEN` or `meta_webhook_whatsapp_api_token`; business line id `META_PHONE_NUMBER_ID` or `meta_phone_number_id` when no sender override is passed.

---

## Quick reference — queue and Lambda names

| Stage | SQS queue (name pattern) | Lambda |
|-------|-------------------------|--------|
| After webhook | `{Environment}-nvidia-demo-ingest` | `IntentProcessor` |
| After intent routing (LLM path) | `{Environment}-nvidia-demo-prellm` | `LlmHandler` |
| After LLM | `{Environment}-nvidia-demo-postllm` | `PostLlmConsumer` |
| Outbound WhatsApp jobs (FIFO) | `{Environment}-nvidia-demo-dispatch-wpp.fifo` | `DispatchWpp` |

Lambda **logical** names in SAM: `WhatsappWebhookTrigger`, `IntentProcessor`, `LlmHandler`, `PostLlmConsumer`, `DispatchWpp`.

---

## Quick reference — SSM parameters (relative to `/{ENVIRONMENT}/nvidia-demo/`)

| Parameter | Used by |
|-----------|---------|
| `meta_webhook_verify_token` | `whatsapp-trigger` (if env verify token not set) |
| `SMART_ROUTER_URL`, `SMART_ROUTER_KEY` | `intent-processor` (+ EC2 bootstrap for key on router host) |
| `main_llm_api`, `main_llm_model`, `main_llm_key`, `main_llm_timeout`, `main_llm_api_version` | `llm-handler` |
| `meta_webhook_whatsapp_api_token`, `meta_phone_number_id` (and legacy aliases) | `wpputil` via `intent-processor` (typing), **`dispatch-wpp`** (send), any Lambda importing `wpputil` |

Stack-created placeholders for LLM/router URL appear in `template.yaml` (`SsmMainLlm*`); populate via `scripts/ssm_put_string_params_from_env.py` / `scripts/ssm_put_secrets_from_env.py` as described in `README.md`.

---

## Suggested study order

1. Read `README.md` mermaid diagram (mental model).  
2. Trace `template.yaml`: `DemoHttpApi` → `WhatsappWebhookTrigger` → queues → each Lambda’s `Environment` and `Events` (include **DispatchWpp** FIFO).  
3. Read `shared/traceutil.py` for `pipeline_trace` shape and helpers.  
4. Read `functions/whatsapp-trigger/app.py` then `functions/intent-processor/app.py` (router call, shortcut vs pre-LLM).  
5. Read `services/semantic-router/main.py` to see how `intent_suggested` is produced.  
6. Deep dive `functions/llm-handler/app.py` alongside one `{Intent}.json` and the matching folder under `rag-context/`.  
7. Read `functions/post-llm/app.py` (dispatch enqueue, performance report) and `functions/dispatch-wpp/app.py` with `shared/wpputil.py`.

This sequence follows the **same order of facts** as a message traveling from WhatsApp through to delivery (or the shortcut path straight to dispatch).
