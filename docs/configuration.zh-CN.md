# 配置参考文档

[English](configuration.en.md) | 中文

本文档描述 stt-transcribe-pipeline 的全部配置选项，涵盖应用设置、流水线预设、执行行为、校验规则以及 HTTP API。

## 1. 概述

应用使用两种独立的配置文件，均为 YAML 格式：

| 类型 | 位置 | 用途 |
|------|----------|---------|
| 应用配置 | `config/config.yml` | 服务器设置、Provider、模型组、鉴权 |
| 流水线预设 | `config/presets/*.yaml` | 流水线定义：block、task、执行流程 |

这两类文件都包含 API 密钥，均已排除在版本控制之外。`config/config.yml` 和 `config/presets/default.yaml` 绝不应提交到仓库。请使用 `config/config.example.yml` 和 `config/presets/default.example.yaml` 作为模板。

**向后兼容回退。** 如果 `config/config.yml` 不存在，加载器会尝试读取 `config/app.yaml` 作为向后兼容的备选路径。若 `config.yml` 存在，则忽略 `app.yaml`。只有 `app.yaml` 成功加载时，来自 `config.yml` 的错误（文件缺失或格式无效）才会被静默忽略。详见 `app/config/loader.py:31-42`。

---

## 2. 应用配置（`config/config.yml`）

应用配置被加载到 `AppConfig` Pydantic 模型中（`app/config/schema.py:9`）。它定义了服务器参数、鉴权、Provider 定义、模型回退组和日志级别。

### 2.0 字段参考

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `host` | 字符串 | `"0.0.0.0"` | 否 | 服务器绑定地址。同样被 `main.py` 中的 uvicorn 入口使用。 |
| `port` | 整型 | `8000` | 否 | 服务器端口。同样被 `main.py` 中的 uvicorn 入口使用。 |
| `api_key` | 字符串 | -- | 是 | 用于鉴权传入请求的 API 密钥。必须匹配正则 `^[a-zA-Z0-9_-]+$`（仅限字母数字、下划线和连字符）。 |
| `default_preset` | 字符串 | -- | 是 | 默认流水线预设名称。必须与 `config/presets/` 内某个 `.yaml` 文件名（不含扩展名）匹配。被 `POST /v1/audio/transcriptions` 端点使用。 |
| `providers` | 字典 | `{}` | 否 | 从 `provider_id` 到 provider 配置的映射。每个条目定义一个 OpenAI 兼容的 API 端点。 |
| `model_groups` | 字典 | `{}` | 否 | 从 `group_name` 到有序的 `"provider_id/model_name"` 列表的映射。在执行流水线时用作回退链。 |
| `log_level` | 字符串 | `"INFO"` | 否 | 应用日志级别。有效值：`TRACE`、`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。输入时忽略大小写，存储时转为大写。 |

### 2.1 Provider 配置

`providers` 部分定义一个或多个 AI API 后端。每个键是一个你自行命名的 provider ID，对应一个 provider 配置：

```yaml
providers:
  provider_id:
    base_url: "http://..."
    api_key: "..."
```

| 子字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `base_url` | 字符串 | 是 | OpenAI 兼容 API 的基地址。示例：`https://api.openai.com/v1`。服务器对 STT 任务 POST 到 `{base_url}/audio/transcriptions`，对 chat 任务 POST 到 `{base_url}/chat/completions`。末尾的斜杠会自动去除。 |
| `api_key` | 字符串 | 是 | 该 provider 的 API 密钥。每次请求均以 `Authorization: Bearer <api_key>` 发送。 |

Provider ID 必须唯一。该 ID 用于模型引用（`provider_id/model_name`）和模型组条目中。

### 2.2 模型组

模型组定义了**回退链**。当任务引用一个组名（而非直接的 `provider/model` 引用）时，系统按列表中的顺序依次尝试每个模型。遇到 `HTTPStatusError` 或 `ConnectError` 时，自动切换到下一个模型。如果链中所有模型都失败，则抛出 `AllModelsFailedError`。

```yaml
model_groups:
  group_name:
    - "provider_id/model_name"      # 最先尝试
    - "provider_id2/model_name2"    # 第一个失败时尝试
    - "provider_id3/model_name3"    # 第二个也失败时尝试
```

**校验规则：**

- 每个条目**必须**包含一个 `/` 字符，用于分隔 `provider_id` 和 `model_name`。缺少 `/` 的条目会在 schema 校验阶段被拒绝。
- 组条目中引用的每个 `provider_id` 都必须存在于 `providers` 部分。这在校验阶段由配置加载器的交叉校验进行检查。
- 模型组在**运行时**进行尝试，而非加载时。加载器仅验证所引用的 provider 是否存在。

### 2.3 完整示例

```yaml
# config/config.yml（模板，请替换为你自己的值）

host: "0.0.0.0"
port: 8000

# 仅限字母数字、下划线和连字符。
api_key: "sk-your-api-key-here"

# 调用 POST /v1/audio/transcriptions 时使用的预设。
default_preset: "default"

# Provider 定义。每个键是唯一的 provider_id。
providers:
  local-qwen:
    base_url: "http://localhost:11434/v1"
    api_key: "none"
  openai:
    base_url: "https://api.openai.com/v1"
    api_key: "sk-your-api-key-here"

# 模型回退组。失败时按顺序尝试。
model_groups:
  smart:
    - "openai/gpt-4o"
    - "openai/gpt-4o-mini"

# 日志级别：TRACE、DEBUG、INFO、WARNING、ERROR、CRITICAL。
log_level: "INFO"
```

### 2.4 HTTP 客户端默认值

应用在启动期间（FastAPI lifespan 中）创建一个共享的 `httpx.AsyncClient`。默认值如下：

| 配置项 | 值 |
|---|---|
| `max_connections` | 100 |
| `max_keepalive_connections` | 20 |
| `connect` 超时 | 10s |
| `read` 超时 | 120s |
| `write` 超时 | 30s |
| `pool` 超时 | 15s |

客户端存储在 `app.state.http_client` 中，在所有 provider 调用间复用。应用关闭时一并关闭。

---

## 3. 流水线预设（`config/presets/*.yaml`）

`config/presets/` 中的每个 `.yaml` 文件定义一个流水线预设。文件名（不含 `.yaml` 扩展名）即为预设名称。

预设的选用由请求的 `model` 字段决定。如果 `model` 的值与 `config/presets/` 目录下某个预设文件名（不含 `.yaml` 扩展名）匹配，则使用该预设；如果 `model` 为空或无法匹配任何预设，则回退到应用配置中的 `default_preset`。当 `model` 非空但不匹配任何预设时，服务器会记录一条警告日志。

### 3.1 PipelineConfig（顶层配置）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `output` | 字符串 | 是 | 对流水线最终结果的引用，格式为 `{block_tag.task_tag.result}`。决定哪个任务的输出作为最终转录结果返回。 |
| `blocks` | list[BlockConfig] | 是 | 有序的流水线 block 列表。按顺序执行。 |

**校验：**

- Block 的 `tag` 值在整个流水线内必须唯一。
- `output` 字段必须严格匹配正则 `^\{[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.result\}$`。
- `output` 字段引用的任务必须真实存在于流水线中（在 `app/engine/resolver.py:84-88` 的配置加载时校验）。
- prompt 中的所有变量引用只能指向**更早 block** 中的任务。前向引用会被拒绝。

### 3.2 BlockConfig

一个 block 是流水线的执行单元。block 之间按顺序执行。每个 block 内的所有 task 通过 `asyncio.gather` 并行执行。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tag` | 字符串 | 是 | 该 block 在流水线内的唯一标识符。 |
| `name` | 字符串 | 否 | 人类可读的名称，仅用于日志输出。 |
| `tasks` | list[TaskConfig] | 是 | 并行执行的一个或多个 task。 |
| `checkpoint` | 字符串 | 否 | 本 block 内某个 task 的 tag，其结果将保存为检查点，用于优雅降级。 |

**校验：**

- Task 的 `tag` 值在同一个 block 内必须唯一。
- 如果设置了 `checkpoint`，它必须与本 block 中某个 task 的 `tag` 匹配。

**检查点行为：**

当 block 设置了 `checkpoint`，其结果会存储为 `last_checkpoint_value`。如果任何**后续** block 发生了 `PipelineError`，流水线会抛出 `PipelineFallback` 异常来代替。API 处理器捕获此异常后，返回最后一次检查点的结果。

这样实现了优雅降级。例如，如果 block 1（原始 STT）设置了检查点，而 block 2（LLM 校正）失败，客户端会收到原始 STT 转录结果而非错误。使用 `verbose_json` 格式时，响应中会包含 `"checkpoint_fallback": true`。

### 3.3 TaskConfig

一个 task 是 block 内的单个 API 调用。

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|---|---|---|---|---|
| `tag` | 字符串 | -- | 是 | 该 task 在所属 block 内的唯一标识符。 |
| `type` | `"chat"` 或 `"transcriptions"` | -- | 是 | 要执行的 API 调用类型。 |
| `model` | 字符串 | -- | 是 | 模型引用。`"provider_id/model_name"`（直接引用）或 `"group_name"`（回退链）。组名不得包含 `/`；直接引用则必须包含。 |
| `need_audio` | 布尔 | `false` | 否 | 是否在请求中包含音频数据。仅对 `"chat"` 类型有意义，此时音频以 base64 编码的 WAV 格式放入 `input_audio` 内容块中。对 `"transcriptions"` 类型无效（该类型始终发送音频）。 |
| `prompt` | 字符串 | `null` | 否 | 提示文本。对 `"transcriptions"` 任务，作为 `prompt` 表单字段发送。对 `"chat"` 任务，用作用户消息的文本内容。支持变量替换。 |
| `max_retries` | 整型 | `0` | 否 | 在整个回退链全部失败后的重试次数。每次重试重新尝试完整链路。 |
| `timeout` | 浮点数 | `null` | 否 | 单次 HTTP 请求的超时时间（秒）。超时的 model 尝试会在回退链内重试（尝试链中下一个 model），并且整条链路最多重试 `max_retries` 次。该值直接传给 `httpx` 请求。 |
| `model_params` | 字典 | `null` | 否 | 直接传递给模型 API 的任意键值参数。对 `"chat"` 任务，这些参数会合并到 JSON 请求体中（如 `temperature`、`top_p`、`max_tokens`、`thinking`）。对 `"transcriptions"` 任务，这些参数会作为额外的表单字段发送。 |

**任务类型说明：**

- **`transcriptions`**：以 multipart 表单形式（`file` 字段，`application/octet-stream` 内容类型）将音频发送到 `{base_url}/audio/transcriptions`，表单数据中包含 `model` 和可选的 `prompt`。返回 JSON 响应中的 `"text"` 字段。

- **`chat`**：以 JSON POST 发送到 `{base_url}/chat/completions`，消息数组包含一条用户消息。消息内容为 content block 列表：始终包含一个 `"text"` block（已替换变量的 prompt），以及可选的 `"input_audio"` block（base64 格式 WAV，当 `need_audio` 为 `true` 时）。返回 `choices[0].message.content` 中的助手消息内容。

### 3.4 变量替换

Task 的 prompt 可以通过以下模式引用**之前已执行** block 的结果：

```
{block_tag.task_tag.result}
```

**规则：**

- 正则模式为 `\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}`。
- 被引用的 `block_tag.task_tag` 必须来自流水线中位于**之前**的 block（严格的先后顺序）。
- 同一 block 内的引用是无效的，会在配置加载时被拒绝。同一个 block 中的所有 task 都并行执行，因此 block 启动时它们的结果还不可用。
- 前向引用（指向流水线中后续 block）会在配置加载时被拒绝。
- 变量替换发生在运行时，在每个 chat task 执行**之前**进行。解析后的字符串直接替换 prompt 中的引用。
- 流水线的 `output` 字段也使用相同模式来选择最终要返回的结果。
- JSON 风格的大括号如 `{"key": "value"}` 不会被替换正则匹配到，因此原样保留。

如果在运行时找不到引用的变量（配置校验通过后理论上不应出现），会抛出 `VariableNotFoundError`。

### 3.5 完整预设示例

```yaml
# config/presets/default.yaml
# 两阶段流水线：STT 转录后跟 LLM 校正

output: "{llm.corrected.result}"

blocks:
  # Block 1：原始语音转文本转录
  # 该 block 设置了检查点，如果后续 LLM 校正失败，
  # 仍然可以返回原始转录结果。
  - tag: stt
    name: "Speech-to-Text Transcription"
    tasks:
      - tag: raw
        type: transcriptions
        model: "openai/whisper-1"
        prompt: null
    checkpoint: "raw"

  # Block 2：LLM 校正
  # 通过变量替换引用 stt.raw.result
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
        model_params:
          temperature: 0.3
```

在这个示例中：

- Block `stt` 首先运行，执行 task `raw`（STT 转录）。结果存储在 `"stt.raw"` 下。
- `checkpoint: "raw"` 保存了这个结果。
- 接下来运行 Block `llm`。它的 prompt 引用了 `{stt.raw.result}`，该引用会被替换为原始转录文本。
- 如果 LLM 调用在耗尽重试次数后仍然失败，检查点回退机制会返回原始 STT 结果。
- 最终输出取自 `{llm.corrected.result}`（如果校正失败，则使用检查点回退值）。

---

## 4. 执行模型

流水线按照 `app/engine/pipeline.py` 中代码定义的确定性顺序执行。

### 执行流程

```
run_pipeline(preset, app_config, http_client, audio_bytes)
  │
  ├── 遍历每个 block（顺序执行）:
  │    │
  │    ├── 遍历 block 中的每个 task（通过 asyncio.gather 并行执行）:
  │    │    │
  │    │    ├── resolve_model(task.model, app_config)
  │    │    │   └── 返回 [(ProviderClient, model_name), ...]
  │    │    │
  │    │    ├── resolve_variables(prompt, results)  [仅 chat 类型]
  │    │    │
│    │    └── _call_task_with_retries(max_retries, models, call_fn)
│    │         │
│    │         ├── call_with_fallback(models, call_fn)
│    │         │    │
│    │         │    ├── 遍历 models 中的每个 (client, model):
│    │         │    │    ├── await call_fn(client, model)  -- 每次 POST 有 httpx 级别的 task timeout
│    │         │    │    │   └── execute_stt_task() 或 execute_chat_task()
│    │         │    │    ├── 遇到 HTTPStatusError/ConnectError/TimeoutException: 尝试下一个
│    │         │    │    └── 遇到其他异常: 立即重新抛出
│    │         │    │
│    │         │    └── 全部失败: 抛出 AllModelsFailedError
│    │         │
│    │         └── 遇到 AllModelsFailedError/ConnectError:
│    │              ├── 重试次数 < max_retries: 重试完整链路
│    │              └── 重试次数 > max_retries: 重新抛出
  │    │
  │    ├── 如果任何 task 抛出异常:
  │    │    ├── last_checkpoint_value 存在: 抛出 PipelineFallback
  │    │    └── 无检查点: 抛出 PipelineError
  │    │
  │    └── 如果设置了 block.checkpoint:
  │         └── 将被检查点标记的 task 结果保存为 last_checkpoint_value
  │
  └── 返回 ResultStore（"block_tag.task_tag" 到结果字符串的字典）
```

### 关键要点

- **block 按顺序执行。** 每个 block 必须完成后下一个才能开始。
- **同一个 block 内的 task 并行执行。** 所有 task 通过 `asyncio.gather(return_exceptions=True)` 收集。如果有 task 抛出异常，则抛出 `PipelineError`。
- **重试逻辑重试的是整个回退链**，而非单个模型。`max_retries: 2` 表示整条链路最多尝试 3 次（1 次初始 + 2 次重试）。
- **超时作用于每次 model 尝试。** 每次 httpx POST 调用都会传入 task 级别的 timeout。超时的 model 尝试会被当作普通的 `HTTPStatusError` 处理，回退到链中的下一个 model。`_call_task_with_retries` 层也会捕获 `httpx.TimeoutException`。
- **可重试的错误：** `AllModelsFailedError`、`httpx.ConnectError`、`httpx.TimeoutException`。
- **不可重试的错误**（以上三种之外的任何错误）会立即重新抛出，不重试。
- **检查点回退**仅在后续 block 失败且之前某个 block 的检查点已保存时才会触发。如果失败的 block 自身也设置了检查点，该检查点此时还不可用（它在 block 成功完成后才保存）。

---

## 5. API 端点

### 5.1 POST `/v1/audio/transcriptions`

转录端点的唯一入口。预设的选择由请求的 `model` 字段决定：

- 如果 `model` 为空或未提供，使用应用配置中的 `default_preset`。
- 如果 `model` 与 `config/presets/` 中某个预设文件名（不含 `.yaml` 扩展名）匹配，则使用该预设。
- 如果 `model` 非空但不匹配任何预设，记录警告日志后回退到 `default_preset`。

### 5.2 GET `/health`

返回最小化的健康检查响应：

```json
{
  "status": "ok"
}
```

### 5.3 请求参数（POST `/v1/audio/transcriptions`）

转录端点接受 `multipart/form-data`，包含以下字段：

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `file` | UploadFile | -- | 要转录的音频文件。必填。最大 25 MB（26214400 字节）。 |
| `model` | 字符串 | `""` | 决定使用哪个流水线预设。空字符串或未提供时使用 `default_preset`。匹配 `config/presets/` 中某个预设文件名（不含 `.yaml`）时，使用对应预设。非空但不匹配时记录警告并回退到 `default_preset`。 |
| `language` | 字符串 | `null` | 为兼容 OpenAI API 而接受。实际未使用。 |
| `prompt` | 字符串 | `null` | 为兼容 OpenAI API 而接受。在 API 层面未使用。流水线级别的 prompt 在预设 YAML 中定义。 |
| `response_format` | 字符串 | `"json"` | 控制响应格式。有效值：`"json"`、`"text"`、`"verbose_json"`。 |
| `temperature` | 浮点数 | `null` | 为兼容 OpenAI API 而接受。实际未使用。 |

如果音频文件超过 25 MB，服务器返回 HTTP 413：

```json
{
  "error": {
    "message": "Audio file too large. Maximum size is 25MB.",
    "type": "invalid_request_error",
    "code": "file_too_large"
  }
}
```

### 5.4 响应格式

**`"json"`（默认）：**
```json
{
  "text": "The final transcription text."
}
```

**`"text"`：** 纯文本响应。不带 JSON 包装。

**`"verbose_json"`：**
```json
{
  "text": "The final transcription text.",
  "pipeline_results": {
    "stt.raw": "Raw STT output",
    "llm.corrected": "Corrected transcription"
  }
}
```

如果使用了检查点回退，`verbose_json` 还会额外包含：
```json
{
  "text": "...",
  "pipeline_results": { ... },
  "checkpoint_fallback": true
}
```

### 5.5 鉴权

除 `/health`、`/healthz`、`/docs`、`/openapi.json` 和 `/redoc` 外的所有端点都需要通过 Bearer 令牌鉴权：

```
Authorization: Bearer <api_key>
```

`<api_key>` 必须与 `config/config.yml` 中的 `api_key` 值匹配。

通过设置环境变量 `SKIP_AUTH=1`（或 `true` 或 `yes`，不区分大小写）可以绕过鉴权。测试套件中使用此方式。

---

## 6. 环境变量

| 变量 | 值 | 说明 |
|---|---|---|
| `SKIP_AUTH` | `1`、`true`、`yes` 或其他任意值 | 当设为 `1`、`true` 或 `yes`（不区分大小写）时，绕过所有端点的 Bearer 令牌鉴权。默认为启用鉴权。 |

---

## 7. 校验规则汇总

所有校验在服务器启动前的配置加载阶段执行。任何规则不通过时，服务器拒绝启动。

### 应用配置（`AppConfig`）

| 规则 | 详情 |
|---|---|
| `api_key` 格式 | 必须匹配 `^[a-zA-Z0-9_-]+$`（字母数字、下划线、连字符）。由 `schema.py` 中的 `@field_validator` 强制执行。 |
| `model_groups` 条目 | 每个条目必须包含 `/`（provider_id/model_name 格式）。由 `@field_validator` 强制执行。 |
| `log_level` | 必须为以下之一：`TRACE`、`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。输入时不区分大小写。 |

### 交叉校验（Load 阶段）

| 规则 | 详情 |
|---|---|
| `default_preset` 存在 | 指定的预设必须以 `.yaml` 文件形式存在于 `config/presets/` 中。 |
| Task 模型引用 | 如果 task 的 `model` 包含 `/`，则 provider_id 必须存在于 `providers` 中。如果不包含 `/`，则必须是 `model_groups` 中的一个键，且该组中的每个条目都引用一个存在的 provider。 |
| 模型组的 provider | 每个 model group 条目中的 `provider_id` 都必须存在于 `providers` 部分中。 |

### 流水线配置（`PipelineConfig`）

| 规则 | 详情 |
|---|---|
| Block tag 唯一 | 同一个流水线中所有 `block.tag` 值必须唯一。 |
| Task tag 唯一 | 同一个 block 内所有 `task.tag` 值必须唯一。 |
| Output 格式 | 必须严格为 `{block_tag.task_tag.result}`。通过正则 `^\{[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.result\}$` 校验。 |
| Output 引用存在 | 被引用的 `block_tag.task_tag` 必须在流水线中某处存在。在 `app/engine/resolver.py:84-88` 中检查。 |
| 变量引用仅限更早 block | Task prompt 中每个 `{block_tag.task_tag.result}` 必须引用**前面** block 的 task。同一 block 内和前向引用均被拒绝。 |
| Checkpoint 指向有效 task | 如果 block 设置了 `checkpoint`，它必须与本 block 内某个 `task.tag` 匹配。 |
| 配置文件已排除版本控制 | `config/config.yml` 和预设文件包含密钥，绝不应提交。 |
