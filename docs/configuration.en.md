# Configuration Reference

English | [中文](configuration.zh-CN.md)

This document describes every configuration option for stt-transcribe-pipeline. It covers application settings, pipeline presets, execution behavior, validation rules, and the HTTP API.

## 1. Overview

The application uses two separate configuration file types, both written in YAML:

| Type | Location | Purpose |
|------|----------|---------|
| App config | `config/config.yml` | Server settings, providers, model groups, auth |
| Pipeline presets | `config/presets/*.yaml` | Pipeline definitions: blocks, tasks, execution flow |

Both file types contain API keys and are excluded from git. The files `config/config.yml` and `config/presets/default.yaml` should never be committed. Use `config/config.example.yml` and `config/presets/default.example.yaml` as templates.

**Legacy fallback.** If `config/config.yml` does not exist, the loader attempts to read `config/app.yaml` as a backward-compatibility fallback. If `config.yml` exists, `app.yaml` is ignored. The error from `config.yml` (missing or invalid) is only suppressed if `app.yaml` loads successfully. See `app/config/loader.py:31-42`.

---

## 2. App Configuration (`config/config.yml`)

The app config is loaded into the `AppConfig` Pydantic model (`app/config/schema.py:9`). It defines server parameters, authentication, provider definitions, model fallback groups, and logging level.

### 2.0 Field Reference

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `host` | string | `"0.0.0.0"` | No | Server bind address. Also used by the uvicorn entry point in `main.py`. |
| `port` | int | `8000` | No | Server port. Also used by the uvicorn entry point in `main.py`. |
| `api_key` | string | -- | Yes | API key for authenticating incoming requests. Must match the regex `^[a-zA-Z0-9_-]+$` (alphanumeric characters, underscores, and hyphens only). |
| `default_preset` | string | -- | Yes | Name of the default pipeline preset. Must match a `.yaml` filename (without extension) inside `config/presets/`. Used by the `POST /v1/audio/transcriptions` endpoint. |
| `providers` | dict | `{}` | No | Mapping of `provider_id` to provider configuration. Each entry defines an OpenAI-compatible API endpoint. |
| `model_groups` | dict | `{}` | No | Mapping of `group_name` to an ordered list of `"provider_id/model_name"` entries. Used as fallback chains during pipeline execution. |
| `log_level` | string | `"INFO"` | No | Application logging level. Valid values: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Case-insensitive on input, stored uppercase. |

### 2.1 Providers

The `providers` section defines one or more AI API backends. Each key is a provider ID (a string of your choosing) that maps to a provider configuration:

```yaml
providers:
  provider_id:
    base_url: "http://..."
    api_key: "..."
```

| Sub-field | Type | Required | Description |
|---|---|---|---|
| `base_url` | string | Yes | Base URL of the OpenAI-compatible API. Example: `https://api.openai.com/v1`. The server POSTs to `{base_url}/audio/transcriptions` for STT tasks and `{base_url}/chat/completions` for chat tasks. Trailing slashes are stripped automatically. |
| `api_key` | string | Yes | API key for this specific provider. Sent as `Authorization: Bearer <api_key>` on every request. |

Provider IDs must be unique. The ID is used in model references (`provider_id/model_name`) and in model group entries.

### 2.2 Model Groups

Model groups define **fallback chains**. When a task references a group name (instead of a direct `provider/model` reference), the system tries each model in the listed order. On `HTTPStatusError` or `ConnectError`, it moves to the next model. If all models in the chain fail, it raises `AllModelsFailedError`.

```yaml
model_groups:
  group_name:
    - "provider_id/model_name"      # tried first
    - "provider_id2/model_name2"    # tried if the first fails
    - "provider_id3/model_name3"    # tried if the second also fails
```

**Validation rules:**

- Each entry **must** contain a `/` character separating `provider_id` and `model_name`. Entries without `/` are rejected at schema-validation time.
- Every `provider_id` referenced in a group entry must exist in the `providers` section. This is checked during cross-validation in the config loader.
- Model groups are tried at **runtime**, not at load time. The loader only verifies that referenced providers exist.

### 2.3 Full Example

```yaml
# config/config.yml (template -- replace values with your own)

host: "0.0.0.0"
port: 8000

# Must contain only alphanumeric characters, underscores, and hyphens.
api_key: "sk-your-api-key-here"

# The preset used when calling POST /v1/audio/transcriptions.
default_preset: "default"

# Provider definitions. Each key is a unique provider_id.
providers:
  local-qwen:
    base_url: "http://localhost:11434/v1"
    api_key: "none"
  openai:
    base_url: "https://api.openai.com/v1"
    api_key: "sk-your-api-key-here"

# Model fallback groups. Tried in order on failure.
model_groups:
  smart:
    - "openai/gpt-4o"
    - "openai/gpt-4o-mini"

# Logging level: TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL.
log_level: "INFO"
```

### 2.4 HTTP Client Defaults

The application creates a single shared `httpx.AsyncClient` during startup (in the FastAPI lifespan). Defaults:

| Setting | Value |
|---|---|
| `max_connections` | 100 |
| `max_keepalive_connections` | 20 |
| `connect` timeout | 10s |
| `read` timeout | 120s |
| `write` timeout | 30s |
| `pool` timeout | 15s |

The client is stored in `app.state.http_client` and reused across all provider calls. It is closed on application shutdown.

---

## 3. Pipeline Presets (`config/presets/*.yaml`)

Each `.yaml` file in `config/presets/` defines one pipeline preset. The filename (without the `.yaml` extension) becomes the preset name.

Presets are loaded into the `PipelineConfig` Pydantic model (`app/config/schema.py:81`). A preset accessed via:

| Endpoint | Preset used |
|---|---|
| `POST /v1/audio/transcriptions` | `default_preset` from app config |
| `POST /{preset_name}/v1/audio/transcriptions` | Named preset (must exist in `config/presets/`) |

If the named preset does not exist, the endpoint returns a 404 with an OpenAI-style error response.

### 3.1 PipelineConfig (Top-Level)

| Field | Type | Required | Description |
|---|---|---|---|
| `output` | string | Yes | A reference to the final pipeline result in `{block_tag.task_tag.result}` format. Determines which task's output is returned as the final transcription. |
| `blocks` | list[BlockConfig] | Yes | Ordered list of pipeline blocks. Executed sequentially. |

**Validation:**

- Block `tag` values must be unique across the entire pipeline.
- The `output` field must match the exact regex `^\{[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.result\}$`.
- The `output` field must reference a task that actually exists in the pipeline (validated at config-load time in `app/engine/resolver.py:84-88`).
- All variable references in prompts must point to tasks from **earlier blocks** only. Forward references are rejected.

### 3.2 BlockConfig

A block is a unit of pipeline execution. Blocks run sequentially. Within each block, all tasks execute in parallel via `asyncio.gather`.

| Field | Type | Required | Description |
|---|---|---|---|
| `tag` | string | Yes | Unique string identifier for this block within the pipeline. |
| `name` | string | No | Human-readable name, used only in log output. |
| `tasks` | list[TaskConfig] | Yes | One or more tasks to execute in parallel. |
| `checkpoint` | string | No | Task tag within this block whose result is saved as a checkpoint for graceful degradation. |

**Validation:**

- Task `tag` values must be unique within the block.
- If `checkpoint` is set, it must match the `tag` of one of the tasks in the same block.

**Checkpoint behavior:**

When a block has `checkpoint` set, its result is stored as `last_checkpoint_value`. If any **subsequent** block fails with a `PipelineError`, the pipeline raises a `PipelineFallback` exception instead. The API handler catches this and returns the last checkpointed result.

This enables graceful degradation. For example, if block 1 (raw STT) has a checkpoint and block 2 (LLM correction) fails, the client receives the raw STT transcription rather than an error. The response includes `"checkpoint_fallback": true` when using `verbose_json` format.

### 3.3 TaskConfig

A task is an individual API call within a block.

| Field | Type | Default | Required | Description |
|---|---|---|---|---|
| `tag` | string | -- | Yes | Unique identifier for this task within the block. |
| `type` | `"chat"` or `"transcriptions"` | -- | Yes | The type of API call to perform. |
| `model` | string | -- | Yes | Model reference. Either `"provider_id/model_name"` (direct) or `"group_name"` (fallback chain). Group names must not contain `/`; direct references must. |
| `need_audio` | bool | `false` | No | Whether to include audio data in the request. Only meaningful for `"chat"` type -- sends the audio as a base64-encoded WAV in an `input_audio` content block. Ignored for `"transcriptions"` type (always sends audio). |
| `prompt` | string | `null` | No | Prompt text. For `"transcriptions"` tasks, sent as the `prompt` form field. For `"chat"` tasks, used as the text content of the user message. Supports variable substitution. |
| `max_retries` | int | `0` | No | Number of retry attempts after the entire fallback chain fails. Each retry re-attempts the full chain. |
| `timeout` | float | `null` | No | Maximum time (in seconds) for a single HTTP request before it times out. A timed-out model attempt is retried within the fallback chain (next model is tried), and the entire chain is re-attempted up to `max_retries` times. Passes `timeout` directly to the `httpx` request. |

**Task types explained:**

- **`transcriptions`**: Sends audio as a multipart form (`file` field with `application/octet-stream` content type) to `{base_url}/audio/transcriptions` with `model` and optional `prompt` as form data. Returns the `"text"` field from the JSON response.

- **`chat`**: Sends a JSON POST to `{base_url}/chat/completions` with a messages array containing a single user message. The message content is a list of content blocks: always a `"text"` block with the resolved prompt, and optionally an `"input_audio"` block (base64 WAV) if `need_audio` is `true`. Returns the assistant message content from `choices[0].message.content`.

### 3.4 Variable Substitution

Task prompts may reference results from **previously executed** blocks using the pattern:

```
{block_tag.task_tag.result}
```

**Rules:**

- The regex pattern is `\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}`.
- The referenced `block_tag.task_tag` must be from a block that appears **earlier** in the pipeline (strict ordering).
- Same-block references are invalid and rejected at config-load time. All tasks in a block run in parallel, so their results do not exist yet when the block starts.
- Forward references (to blocks later in the pipeline) are rejected at config-load time.
- Variable resolution happens at runtime, **before** each chat task is executed. The resolved string replaces in the prompt.
- The `output` field of the pipeline uses the same pattern to select the final result to return.
- JSON-like braces such as `{"key": "value"}` are not matched by the substitution regex, so they pass through untouched.

If a referenced variable cannot be found at runtime (should not happen if config validation passed), a `VariableNotFoundError` is raised.

### 3.5 Full Preset Example

```yaml
# config/presets/default.yaml
# Two-block pipeline: STT transcription followed by LLM correction.

output: "{llm.corrected.result}"

blocks:
  # Block 1: Raw speech-to-text transcription
  # This block has a checkpoint so that if the LLM correction
  # fails, we still return the raw transcription.
  - tag: stt
    name: "Speech-to-Text Transcription"
    tasks:
      - tag: raw
        type: transcriptions
        model: "openai/whisper-1"
        prompt: null
    checkpoint: "raw"

  # Block 2: LLM correction
  # References stt.raw.result via variable substitution.
  - tag: llm
    name: "LLM Correction"
    tasks:
      - tag: corrected
        type: chat
        model: "smart"
        need_audio: false
        prompt: |
          Fix the following transcription for spelling, grammar, and punctuation.
          Return only the corrected text without any explanation.

          Raw transcription:
          {stt.raw.result}
        max_retries: 2
```

In this example:

- Block `stt` runs first, executing task `raw` (STT transcription). The result is stored as `"stt.raw"`.
- The `checkpoint: "raw"` saves this result.
- Block `llm` runs next. Its prompt references `{stt.raw.result}` which resolves to the raw transcription text.
- If the LLM call fails after all retries, the checkpoint fallback returns the raw STT result.
- The final output is taken from `{llm.corrected.result}` (or the checkpoint fallback value if correction failed).

---

## 4. Execution Model

The pipeline executes in a deterministic order defined by the code in `app/engine/pipeline.py`.

### Execution flow

```
run_pipeline(preset, app_config, http_client, audio_bytes)
  │
  ├── for each block (sequential):
  │    │
  │    ├── for each task in block (parallel via asyncio.gather):
  │    │    │
  │    │    ├── resolve_model(task.model, app_config)
  │    │    │   └── Returns [(ProviderClient, model_name), ...]
  │    │    │
  │    │    ├── resolve_variables(prompt, results)  [chat only]
  │    │    │
│    │    └── _call_task_with_retries(max_retries, models, call_fn)
│    │         │
│    │         ├── call_with_fallback(models, call_fn)
│    │         │    │
│    │         │    ├── for each (client, model) in models:
│    │         │    │    ├── await call_fn(client, model)  -- each POST has per-task timeout via httpx
│    │         │    │    │   └── execute_stt_task() OR execute_chat_task()
│    │         │    │    ├── On HTTPStatusError/ConnectError/TimeoutException: try next
│    │         │    │    └── On other exception: re-raise immediately
│    │         │    │
│    │         │    └── All failed: raise AllModelsFailedError
│    │         │
│    │         └── On AllModelsFailedError/ConnectError:
│    │              ├── attempt < max_retries: retry full chain
│    │              └── attempt > max_retries: re-raise
  │    │
  │    ├── If any task raised an exception:
  │    │    ├── last_checkpoint_value exists: raise PipelineFallback
  │    │    └── No checkpoint: raise PipelineError
  │    │
  │    └── If block.checkpoint is set:
  │         └── Save checkpointed task result as last_checkpoint_value
  │
  └── Return ResultStore (dict of "block_tag.task_tag" -> result string)
```

### Key points

- **Blocks are sequential.** Each block must finish before the next begins.
- **Tasks within a block are parallel.** All tasks are gathered with `asyncio.gather(return_exceptions=True)`. If any task raised an exception, a `PipelineError` is raised.
- **Retry logic retries the entire fallback chain**, not individual models. `max_retries: 2` means the full chain is tried up to 3 times total (1 initial + 2 retries).
- **Timeout applies per model attempt.** Each `httpx` POST call passes the per-task timeout. A timed-out model attempt is caught like any other `HTTPStatusError` and the next model in the chain is tried. The overall `_call_task_with_retries` layer catches `httpx.TimeoutException` too.
- **Retryable errors:** `AllModelsFailedError`, `httpx.ConnectError`, `httpx.TimeoutException`.
- **Non-retryable errors** (anything other than the above three) are re-raised immediately with no retry.
- **Checkpoint fallback** only triggers when a later block fails and a previous block's checkpoint was saved. If the failing block itself has a checkpoint, that checkpoint is not yet available (it is saved after the block completes successfully).

---

## 5. API Endpoints

### 5.1 POST `/v1/audio/transcriptions`

Runs the pipeline using the `default_preset` defined in `config/config.yml`.

### 5.2 POST `/{preset_name}/v1/audio/transcriptions`

Runs the pipeline using the named preset. If the preset does not exist, returns 404:

```json
{
  "error": {
    "message": "Preset 'xyz' not found.",
    "type": "invalid_request_error",
    "code": "preset_not_found"
  }
}
```

### 5.3 GET `/health`

Returns a minimal health check:

```json
{
  "status": "ok"
}
```

### 5.4 Request Parameters (Transcription Endpoints)

Both POST transcription endpoints accept `multipart/form-data` with the following fields:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file` | UploadFile | -- | Audio file to transcribe. Required. Maximum 25 MB (26214400 bytes). |
| `model` | string | `""` | Accepted for OpenAI API compatibility. Not used. The pipeline configuration determines which models are called. |
| `language` | string | `null` | Accepted for OpenAI API compatibility. Not used. |
| `prompt` | string | `null` | Accepted for OpenAI API compatibility. Not used at the API level. Pipeline-level prompts are defined in preset YAML. |
| `response_format` | string | `"json"` | Controls response format. Valid values: `"json"`, `"text"`, `"verbose_json"`. |
| `temperature` | float | `null` | Accepted for OpenAI API compatibility. Not used. |

If the audio file exceeds 25 MB, the server returns HTTP 413:

```json
{
  "error": {
    "message": "Audio file too large. Maximum size is 25MB.",
    "type": "invalid_request_error",
    "code": "file_too_large"
  }
}
```

### 5.5 Response Formats

**`"json"` (default):**
```json
{
  "text": "The final transcription text."
}
```

**`"text"`:** Plain text response. No JSON wrapper.

**`"verbose_json"`:**
```json
{
  "text": "The final transcription text.",
  "pipeline_results": {
    "stt.raw": "Raw STT output",
    "llm.corrected": "Corrected transcription"
  }
}
```

If a checkpoint fallback was used, `verbose_json` also includes:
```json
{
  "text": "...",
  "pipeline_results": { ... },
  "checkpoint_fallback": true
}
```

### 5.6 Authentication

All endpoints except `/health`, `/healthz`, `/docs`, `/openapi.json`, and `/redoc` require authentication via Bearer token:

```
Authorization: Bearer <api_key>
```

The `<api_key>` must match the `api_key` value from `config/config.yml`.

Authentication can be bypassed by setting the environment variable `SKIP_AUTH=1` (or `true` or `yes`, case-insensitive). This is used in the test suite.

---

## 6. Environment Variables

| Variable | Values | Description |
|---|---|---|
| `SKIP_AUTH` | `1`, `true`, `yes`, or any other value | When set to `1`, `true`, or `yes` (case-insensitive), bypasses Bearer token authentication for all endpoints. Defaults to authentication enabled. |

---

## 7. Validation Rules Summary

All validation is performed at config-load time before the server starts. If any rule fails, the server refuses to start.

### App Config (`AppConfig`)

| Rule | Detail |
|---|---|
| `api_key` format | Must match `^[a-zA-Z0-9_-]+$` (alphanumeric, underscores, hyphens). Enforced by `@field_validator` in `schema.py`. |
| `model_groups` entries | Every entry must contain `/` (provider_id/model_name format). Enforced by `@field_validator`. |
| `log_level` | Must be one of: `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. Case-insensitive on input. |

### Cross-Entity (Loader)

| Rule | Detail |
|---|---|
| `default_preset` exists | The named preset must exist as a `.yaml` file in `config/presets/`. |
| Task model references | If a task `model` value contains `/`, the provider_id must exist in `providers`. If it does not contain `/`, it must be a key in `model_groups`, and each entry in that group must reference an existing provider. |
| Model group providers | Every `provider_id` in every model group entry must exist in the `providers` section. |

### Pipeline Config (`PipelineConfig`)

| Rule | Detail |
|---|---|
| Block tags unique | All `block.tag` values in a pipeline must be unique. |
| Task tags unique | All `task.tag` values within a single block must be unique. |
| Output format | Must be exactly `{block_tag.task_tag.result}`. Validated by regex `^\{[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.result\}$`. |
| Output reference exists | The referenced `block_tag.task_tag` must exist somewhere in the pipeline. Checked in `app/engine/resolver.py:84-88`. |
| Variable refs to earlier blocks only | Every `{block_tag.task_tag.result}` in a task prompt must reference a block.task from a **previous** block. Same-block and forward references are rejected. |
| Checkpoint references valid task | If a block has `checkpoint`, it must match a `task.tag` within that same block. |
| Config files are gitignored | `config/config.yml` and preset files contain secrets. Never commit them. |
