# stt-transcribe-pipeline

A configurable, multistage STT (speech-to-text) transcription pipeline built with FastAPI. Receives audio, runs it through a block-based pipeline of STT and LLM correction steps via OpenAI-compatible provider APIs, and returns corrected text.

English | [中文](README.zh-CN.md)

## Overview

stt-transcribe-pipeline is a FastAPI server that wraps any OpenAI-compatible STT and LLM provider API into a configurable, multistage processing pipeline. Audio comes in one end, corrected text comes out the other, with full control over the intermediate steps.

The pipeline is defined in YAML as a sequence of blocks. Blocks execute sequentially. Tasks within a block execute in parallel. Later blocks can reference results from earlier blocks through variable substitution. If a block fails, the system falls back to the last checkpointed result for graceful degradation. Model groups provide automatic provider failover chains.

The input and output formats follow the OpenAI API conventions, making this a drop-in replacement for OpenAI's audio transcription endpoint in any client that supports it.

## Features

- Block-based pipelines -- define multi-stage workflows (STT, translation, correction, summarization) in YAML
- Parallel task execution -- tasks within a block run concurrently via asyncio.gather
- Model fallback chains -- group multiple models together; if the first fails, the next one is tried automatically
- Checkpoint-based fault tolerance -- if a later block fails, fall back to the last successful checkpoint; the request still returns useful output
- OpenAI-compatible API -- accepts multipart form requests and returns OpenAI-style JSON; works with any existing OpenAI audio client
- Multi-provider support -- configure any number of OpenAI-compatible STT and LLM providers (local or cloud)
- Variable substitution -- pass results between blocks using {block_tag.task_tag.result} syntax
- Preset system -- switch between different pipeline configurations via the `model` request field
- Per-task model parameters -- pass arbitrary parameters like `temperature`, `top_p`, or `thinking` to individual tasks via `model_params`
- Docker support -- multi-stage build with pre-built images on Docker Hub
- Bearer token auth -- API protected by Bearer token; bypass with SKIP_AUTH=1 for local development

## Quick Start

### Docker Compose (Recommended)

1. Create a working directory and download the necessary files:

```bash
mkdir -p stt-transcribe-pipeline/config/presets && cd stt-transcribe-pipeline

# Download docker-compose.yml and example config
curl -fsSLO https://raw.githubusercontent.com/uuz233/stt-transcribe-pipeline/master/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/uuz233/stt-transcribe-pipeline/master/config/config.example.yml -o config/config.yml
```

2. Edit `config/config.yml` with your providers and API keys, then create your pipeline preset in `config/presets/default.yaml`. See the [Configuration Guide](docs/configuration.en.md) for the full reference.

3. Start with Docker Compose:

```bash
docker compose up -d
```

The server will be available at `http://localhost:8000`.

### Manual Setup

Requires Python 3.10 or higher.

```bash
# Clone the repository
git clone https://github.com/uuz233/stt-transcribe-pipeline.git
cd stt-transcribe-pipeline

# Create config files
cp config/config.example.yml config/config.yml

# Edit config/config.yml with your providers and pipeline settings

# Install dependencies
pip install -e .

# Start the server
python main.py
```

The server listens on the host and port defined in `config/config.yml` (defaults to `0.0.0.0:8000`).

For local development, you can disable authentication:

```bash
SKIP_AUTH=1 python main.py
```

## Usage

### Transcribe Audio

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -F "file=@audio.wav" \
  -F "model=default" \
  -F "language=en" \
  -F "response_format=json"
```

The `model` field selects which pipeline preset to use. If `model` matches a preset filename in `config/presets/` (without the `.yaml` extension), that preset runs. If empty or unmatched, the `default_preset` from `config/config.yml` is used.

### Health Check

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

### Response Formats

The `response_format` parameter controls the shape of the returned response:

| Format | Description |
|--------|-------------|
| `json` | Default. Returns `{"text": "transcription result"}` |
| `text` | Returns plain text with no wrapping JSON |
| `verbose_json` | Returns `{"text": "...", "pipeline_results": {...}}` including all intermediate block outputs |

### Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file` | file | required | Audio file (max 25MB) |
| `model` | string | "" | Selects which pipeline preset to use. Matches preset filenames in `config/presets/`. Empty or unmatched falls back to `default_preset`. |
| `language` | string | null | Accepted for OpenAI API compatibility. |
| `prompt` | string | null | Accepted for OpenAI API compatibility. |
| `response_format` | string | "json" | Output format: json, text, or verbose_json |
| `temperature` | float | null | Accepted for OpenAI API compatibility. |

## Configuration

Configuration is split across two file types:

- **`config/config.yml`** -- application-level settings: server host/port, API key, provider definitions, model groups, and the default preset name.
- **`config/presets/*.yaml`** -- pipeline preset definitions: the ordered list of blocks, each containing tasks with their prompts, parameters, and output settings.

See [Configuration Guide](docs/configuration.en.md) for the complete reference.

### Example config.yml

```yaml
host: "0.0.0.0"
port: 8000
api_key: "sk-your-api-key-here"
default_preset: "default"

providers:
  local-qwen:
    base_url: "http://localhost:11434/v1"
    api_key: "none"
  openai:
    base_url: "https://api.openai.com/v1"
    api_key: "sk-your-openai-key-here"

model_groups:
  smart:
    - "openai/gpt-4o"
    - "openai/gpt-4o-mini"

log_level: "INFO"
```

Model groups are referenced by name in preset task definitions. When a task references a group, the system tries each model in order until one succeeds. Direct model references use the `provider_id/model_name` format.

## How It Works

### Pipeline Execution Model

A pipeline is an ordered list of blocks. Each block contains one or more tasks.

```
Pipeline
  Block 1 ("stt")
    Task: transcribe
  Block 2 ("correction")
    Task: correct_grammar    Task: fix_punctuation
  Block 3 ("translation")
    Task: translate
```

Blocks execute sequentially. Block 2 waits for Block 1 to complete before starting.

Tasks within a block execute in parallel. Both `correct_grammar` and `fix_punctuation` in Block 2 start at the same time via asyncio.gather.

Variable substitution allows later blocks to reference the output of earlier blocks. The syntax is `{block_tag.task_tag.result}`. For example, in Block 3 you might reference the output of the correction task from Block 2 as `{correction.correct_grammar.result}`.

A block can declare a checkpoint by setting the `checkpoint` field to one of its task tags. If a later block fails and retries are exhausted, the pipeline falls back to the last checkpointed result rather than returning an error. This means a request can still return useful transcription even if the correction step fails.

Model fallback chains are configured as named groups in `config/config.yml`. When a task references a group name, the system tries each model in the group in order. This lets you specify a primary provider with a cheaper or local fallback.

## Project Structure

```
stt-transcribe-pipeline/
├── main.py                  # FastAPI application, lifespan, auth middleware
├── app/
│   ├── api/
│   │   ├── transcription.py # POST /v1/audio/transcriptions endpoint
│   │   └── health.py        # GET /health
│   ├── config/
│   │   ├── schema.py        # Pydantic configuration models
│   │   └── loader.py        # YAML loading and cross-validation
│   ├── engine/
│   │   ├── pipeline.py      # Block orchestrator with retry and checkpoint logic
│   │   └── resolver.py      # Variable substitution ({block.task.result})
│   └── services/
│       ├── providers.py     # Provider client, model resolution, fallback chains
│       ├── stt.py           # STT task execution
│       └── llm.py           # Chat/LLM task execution
├── config/
│   ├── config.example.yml   # Configuration template
│   └── presets/             # Pipeline preset definitions
├── docs/
│   ├── configuration.en.md  # Configuration reference (English)
│   └── configuration.zh-CN.md # Configuration reference (Chinese)
├── tests/                   # pytest test suite
├── docker-compose.yml       # Docker Compose configuration
├── Dockerfile               # Multi-stage Docker build
└── pyproject.toml           # Project metadata and dependencies
```

## Development

```bash
# Install with development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with verbose output
pytest -v

# Run specific test file
pytest tests/test_pipeline.py -v
```

Tests run with authentication disabled by default via `SKIP_AUTH=1` set in the root `conftest.py`. The test suite uses `pytest-httpx` for mocking HTTP requests.

## License

MIT License. Copyright (c) 2026 路过的幽幽子
