# Configuration & API Reference

English | [中文](configuration.zh-CN.md)

## 1. API Usage

This is an OpenAI-compatible audio transcription endpoint. If your client already works with OpenAI's `/v1/audio/transcriptions`, it works with this server — just change the base URL and API key.

### 1.1 Endpoint

```
POST /v1/audio/transcriptions
```

### 1.2 Authentication

All requests require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <your-api-key>
```

The API key is configured in `config/config.yml` (the `api_key` field). For local development, you can disable authentication entirely:

```bash
SKIP_AUTH=1 python main.py
```

The following paths are always accessible without authentication: `/health`, `/healthz`, `/docs`, `/openapi.json`, `/redoc`.

### 1.3 Quick Example

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer your-api-key" \
  -F "file=@recording.wav" \
  -F "model=default" \
  -F "response_format=json"
```

### 1.4 Request Parameters

The endpoint accepts `multipart/form-data` with the following fields:

| Parameter | Type | Default | Required | Description |
|-----------|------|---------|----------|-------------|
| `file` | file | — | Yes | Audio file to transcribe. Maximum 25 MB. |
| `model` | string | `""` | No | Selects which pipeline preset to use. Must match a filename (without `.yaml`) in `config/presets/`. If empty or unmatched, falls back to the `default_preset` defined in `config/config.yml`. |
| `response_format` | string | `"json"` | No | Response format: `"json"`, `"text"`, or `"verbose_json"`. See below. |
| `language` | string | `null` | No | Accepted for OpenAI API compatibility. Not used by the pipeline. |
| `prompt` | string | `null` | No | Accepted for OpenAI API compatibility. Not used at the API level. Pipeline prompts are defined in preset YAML files. |
| `temperature` | float | `null` | No | Accepted for OpenAI API compatibility. Not used by the pipeline. |

### 1.5 Response Formats

**`"json"` (default)**

```json
{
  "text": "The transcribed and processed text."
}
```

**`"text"`**

Returns plain text with no JSON wrapper.

**`"verbose_json"`**

Returns the final text plus all intermediate results from each pipeline step:

```json
{
  "text": "The transcribed and processed text.",
  "pipeline_results": {
    "stt.raw": "Raw STT output...",
    "llm.corrected": "Corrected output..."
  }
}
```

If a checkpoint fallback was used (a later pipeline step failed, so the server returned an earlier step's result), the response also includes:

```json
{
  "text": "...",
  "pipeline_results": { ... },
  "checkpoint_fallback": true
}
```

### 1.6 Error Responses

All errors follow the OpenAI error format:

```json
{
  "error": {
    "message": "Description of what went wrong.",
    "type": "error_type",
    "code": "error_code"
  }
}
```

| HTTP Status | Code | When |
|-------------|------|------|
| 401 | `invalid_api_key` | Missing or incorrect API key. |
| 413 | `file_too_large` | Audio file exceeds 25 MB. |
| 500 | `pipeline_error` | A pipeline task failed with no checkpoint fallback available. |
| 500 | `all_models_failed` | Every model in the fallback chain failed. |

### 1.7 Health Check

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

### 1.8 OpenAPI / Swagger

Interactive API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc) when the server is running.

---

## 2. App Configuration (`config/config.yml`)

The app config defines server settings, provider connections, model groups, and authentication. Copy `config/config.example.yml` to get started:

```bash
cp config/config.example.yml config/config.yml
```

> **Security**: `config/config.yml` contains API keys and is gitignored. Never commit it.

### 2.1 Full Example

```yaml
host: "0.0.0.0"
port: 8000
api_key: "your-api-key"
default_preset: "default"

providers:
  local-whisper:
    base_url: "http://localhost:11434/v1"
    api_key: "none"
  openai:
    base_url: "https://api.openai.com/v1"
    api_key: "sk-your-openai-key"

model_groups:
  smart:
    - "openai/gpt-4o"
    - "openai/gpt-4o-mini"

log_level: "INFO"
```

### 2.2 Field Reference

| Field | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `host` | string | `"0.0.0.0"` | No | Server bind address. |
| `port` | int | `8000` | No | Server port. |
| `api_key` | string | — | Yes | API key for authenticating incoming requests. Alphanumeric, underscores, and hyphens only (`^[a-zA-Z0-9_-]+$`). |
| `default_preset` | string | — | Yes | Default pipeline preset name. Must match a `.yaml` filename (without extension) in `config/presets/`. |
| `providers` | dict | `{}` | No | Provider definitions (see below). |
| `model_groups` | dict | `{}` | No | Model fallback groups (see below). |
| `log_level` | string | `"INFO"` | No | Logging level: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

### 2.3 Providers

Each provider is an OpenAI-compatible API backend:

```yaml
providers:
  my-provider:
    base_url: "https://api.example.com/v1"
    api_key: "key-here"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `base_url` | string | Yes | Base URL of the API. The server appends `/audio/transcriptions` for STT and `/chat/completions` for chat. Trailing slashes are stripped. |
| `api_key` | string | Yes | Sent as `Authorization: Bearer <api_key>` with every request to this provider. |

The provider ID (the YAML key, e.g. `my-provider`) is used when referencing models: `my-provider/whisper-1`.

### 2.4 Model Groups

Model groups define **fallback chains** — if the first model fails, the next one is tried automatically:

```yaml
model_groups:
  smart:
    - "openai/gpt-4o"        # tried first
    - "openai/gpt-4o-mini"   # tried if gpt-4o fails
    - "local/llama3"          # tried if gpt-4o-mini also fails
```

Each entry must be in `provider_id/model_name` format. Every `provider_id` must exist in the `providers` section.

In a pipeline preset, reference a group by name (e.g. `model: "smart"`) instead of a specific model. The system tries each model in order on `HTTPStatusError`, `ConnectError`, or `TimeoutException`. If all fail, the task raises `AllModelsFailedError`.

### 2.5 Environment Variables

| Variable | Values | Description |
|----------|--------|-------------|
| `SKIP_AUTH` | `1`, `true`, `yes` | Bypass Bearer token authentication. Defaults to authentication enabled. |

---

## 3. Pipeline Presets (`config/presets/*.yaml`)

Each `.yaml` file in `config/presets/` defines a pipeline preset. The filename (without `.yaml`) is the preset name. The `model` field in the API request selects which preset to run.

> **Security**: Preset files may reference model groups and are gitignored by default.

### 3.1 How Presets Work

A pipeline is an ordered list of **blocks**. Each block contains one or more **tasks**.

- **Blocks run sequentially** — block 2 waits for block 1 to finish.
- **Tasks within a block run in parallel** — all tasks in a block start at the same time.
- **Later blocks can reference earlier block results** via `{block_tag.task_tag.result}` in prompts.
- **Checkpoints** allow graceful degradation — if a later block fails, the server returns the last checkpointed result instead of an error.

### 3.2 Full Preset Example

```yaml
# config/presets/default.yaml

output: "{llm.corrected.result}"

blocks:
  # Block 1: Speech-to-text
  - tag: stt
    name: "Transcription"
    tasks:
      - tag: raw
        type: transcriptions
        model: "openai/whisper-1"
    checkpoint: "raw"    # save this result as fallback

  # Block 2: LLM correction (uses Block 1's result)
  - tag: llm
    name: "Correction"
    tasks:
      - tag: corrected
        type: chat
        model: "smart"   # uses model group fallback chain
        prompt: |
          Fix spelling, grammar, and punctuation.
          Return only the corrected text.

          Raw transcription:
          {stt.raw.result}
        max_retries: 2
        model_params:
          temperature: 0.3
```

What happens:

1. Block `stt` transcribes the audio. Result saved as checkpoint.
2. Block `llm` sends the transcription to an LLM for correction. `{stt.raw.result}` is replaced with the actual transcription text.
3. If the LLM fails after retries, the server returns the raw transcription (checkpoint fallback) instead of an error.
4. The `output` field determines which task's result is the final answer.

### 3.3 Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `output` | string | Yes | Which task's result to return. Format: `{block_tag.task_tag.result}`. |
| `blocks` | list | Yes | Ordered list of blocks. |

### 3.4 Block Fields

| Field | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `tag` | string | — | Yes | Unique identifier for this block. |
| `name` | string | `null` | No | Human-readable label (for logs only). |
| `tasks` | list | — | Yes | Tasks to run in parallel within this block. |
| `checkpoint` | string | `null` | No | Tag of a task in this block whose result is saved as a fallback. If a later block fails, this value is returned instead of an error. |

### 3.5 Task Fields

| Field | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `tag` | string | — | Yes | Unique identifier within the block. |
| `type` | string | — | Yes | `"transcriptions"` (STT) or `"chat"` (LLM). |
| `model` | string | — | Yes | Either `"provider_id/model_name"` (direct) or a model group name (fallback chain). |
| `need_audio` | bool | `false` | No | Send audio to this task. Always true for `transcriptions`. For `chat`, sends audio as base64-encoded WAV. |
| `prompt` | string | `null` | No | Prompt text. Supports `{block.task.result}` variable substitution. For `transcriptions`, sent as the `prompt` form field. For `chat`, used as the user message. |
| `max_retries` | int | `0` | No | How many times to retry the entire fallback chain after all models fail. `0` = no retries. |
| `timeout` | float | `null` | No | Per-request timeout in seconds. When not set, the global HTTP client timeouts apply (connect 10s, read 120s, write 30s). |
| `model_params` | dict | `null` | No | Extra parameters passed to the model API (e.g. `temperature`, `top_p`, `max_tokens`). Merged into the request body for `chat`, added as form fields for `transcriptions`. |

**Task types:**

- **`transcriptions`** — POSTs audio as multipart form to `{base_url}/audio/transcriptions`. Returns the `text` field from the response. Streaming is disabled (`stream: false`).
- **`chat`** — POSTs JSON to `{base_url}/chat/completions` with a user message containing the prompt (and optionally base64 audio). Returns `choices[0].message.content`. Streaming is disabled (`stream: false`).

### 3.6 Variable Substitution

Use `{block_tag.task_tag.result}` in prompts to reference results from earlier blocks:

```yaml
prompt: "Correct this: {stt.raw.result}"
```

Rules:
- Can only reference tasks from **earlier** blocks (not the same block — tasks in a block run in parallel).
- Forward references are rejected at startup.
- JSON braces like `{"key": "value"}` are not affected.

---

## 4. Internals

This section covers implementation details for contributors and advanced users.

### 4.1 Execution Flow

```
run_pipeline(preset, app_config, http_client, audio_bytes)
  │
  ├── for each block (sequential):
  │    │
  │    ├── for each task in block (parallel via asyncio.gather):
  │    │    ├── resolve_model() → [(ProviderClient, model_name), ...]
  │    │    ├── resolve_variables(prompt, results)  [chat only]
  │    │    └── _call_task_with_retries()
  │    │         ├── call_with_fallback()
  │    │         │    ├── try each (client, model):
  │    │         │    │    ├── call_fn(client, model)
  │    │         │    │    ├── HTTPStatusError/ConnectError/TimeoutException → next model
  │    │         │    │    └── other exception → re-raise immediately
  │    │         │    └── all failed → AllModelsFailedError
  │    │         └── AllModelsFailedError/ConnectError → retry or re-raise
  │    │
  │    ├── any task exception?
  │    │    ├── checkpoint exists → PipelineFallback
  │    │    └── no checkpoint → PipelineError
  │    │
  │    └── block.checkpoint set?
  │         └── save result as last_checkpoint_value
  │
  └── return ResultStore
```

### 4.2 Retry and Fallback Details

- **Retry scope**: `max_retries` retries the **entire fallback chain**, not individual models. `max_retries: 2` = up to 3 total attempts (1 initial + 2 retries).
- **Retryable errors**: `AllModelsFailedError`, `httpx.ConnectError`, `httpx.TimeoutException`.
- **Non-retryable errors**: Everything else re-raises immediately.
- **Checkpoint fallback** only triggers if a **previous** block's checkpoint was saved. The failing block's own checkpoint is not yet available.

### 4.3 HTTP Client Defaults

A single shared `httpx.AsyncClient` is created at startup with these defaults:

| Setting | Value |
|---------|-------|
| `max_connections` | 100 |
| `max_keepalive_connections` | 20 |
| `connect` timeout | 10s |
| `read` timeout | 120s |
| `write` timeout | 30s |
| `pool` timeout | 15s |

When a task does not set `timeout`, these global timeouts apply.

### 4.4 Legacy Config Fallback

If `config/config.yml` does not exist, the loader attempts `config/app.yaml` as a backward-compatibility fallback. If `config.yml` exists, `app.yaml` is ignored.

### 4.5 Validation Rules

All validation runs at startup. If any rule fails, the server refuses to start.

**App config:**

| Rule | Detail |
|------|--------|
| `api_key` format | `^[a-zA-Z0-9_-]+$` |
| `model_groups` entries | Every entry must contain `/`. |
| `log_level` | Must be one of: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `default_preset` | Must match a `.yaml` file in `config/presets/`. |
| Model references | Every `provider_id` in model groups and task model fields must exist in `providers`. |

**Pipeline config:**

| Rule | Detail |
|------|--------|
| Block tags unique | Across the entire pipeline. |
| Task tags unique | Within each block. |
| `output` format | Must be `{block_tag.task_tag.result}`. |
| `output` reference | Must point to an existing task. |
| Variable refs | Can only reference earlier blocks. Same-block and forward refs are rejected. |
| `checkpoint` | Must match a task tag within the same block. |
