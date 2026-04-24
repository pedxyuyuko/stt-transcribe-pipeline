# 配置与 API 参考

[English](configuration.en.md) | 中文

## 1. API 使用

本服务提供 OpenAI 兼容的音频转录接口。如果你的客户端已经对接了 OpenAI 的 `/v1/audio/transcriptions`，只需更换 base URL 和 API key 即可接入。

### 1.1 接口地址

```
POST /v1/audio/transcriptions
```

### 1.2 认证

所有请求需要在 `Authorization` 头中携带 Bearer 令牌：

```
Authorization: Bearer <你的api-key>
```

API key 在 `config/config.yml` 的 `api_key` 字段中配置。本地开发时可以跳过认证：

```bash
SKIP_AUTH=1 python main.py
```

以下路径无需认证：`/health`、`/healthz`、`/docs`、`/openapi.json`、`/redoc`。

### 1.3 快速示例

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer your-api-key" \
  -F "file=@recording.wav" \
  -F "model=default" \
  -F "response_format=json"
```

### 1.4 请求参数

接口接受 `multipart/form-data`，支持以下字段：

| 参数 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `file` | 文件 | — | 是 | 要转录的音频文件，最大 25 MB。 |
| `model` | 字符串 | `""` | 否 | 指定使用哪个流水线预设。值需匹配 `config/presets/` 下的文件名（不含 `.yaml`）。为空或不匹配时回退到 `config/config.yml` 中的 `default_preset`。 |
| `response_format` | 字符串 | `"json"` | 否 | 响应格式：`"json"`、`"text"` 或 `"verbose_json"`，详见下方。 |
| `language` | 字符串 | `null` | 否 | 为兼容 OpenAI API 而接受，流水线内部不使用。 |
| `prompt` | 字符串 | `null` | 否 | 为兼容 OpenAI API 而接受，API 层面不使用。流水线的 prompt 在预设 YAML 中定义。 |
| `temperature` | 浮点数 | `null` | 否 | 为兼容 OpenAI API 而接受，流水线内部不使用。 |

### 1.5 响应格式

**`"json"`（默认）**

```json
{
  "text": "转录和处理后的文本。"
}
```

**`"text"`**

返回纯文本，不带 JSON 包装。

**`"verbose_json"`**

返回最终文本以及流水线每一步的中间结果：

```json
{
  "text": "转录和处理后的文本。",
  "pipeline_results": {
    "stt.raw": "原始 STT 输出...",
    "llm.corrected": "纠正后的输出..."
  }
}
```

如果触发了检查点回退（后续步骤失败，服务器返回了较早步骤的结果），响应中还会包含：

```json
{
  "text": "...",
  "pipeline_results": { ... },
  "checkpoint_fallback": true
}
```

### 1.6 错误响应

所有错误遵循 OpenAI 错误格式：

```json
{
  "error": {
    "message": "错误描述。",
    "type": "error_type",
    "code": "error_code"
  }
}
```

| HTTP 状态码 | Code | 触发条件 |
|-------------|------|----------|
| 401 | `invalid_api_key` | 缺少或错误的 API key。 |
| 413 | `file_too_large` | 音频文件超过 25 MB。 |
| 500 | `pipeline_error` | 流水线任务失败且没有检查点可以回退。 |
| 500 | `all_models_failed` | 回退链中所有模型均失败。 |

### 1.7 健康检查

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

### 1.8 OpenAPI / Swagger

服务运行时，可通过 `/docs`（Swagger UI）和 `/redoc`（ReDoc）访问交互式 API 文档。

---

## 2. 应用配置（`config/config.yml`）

应用配置定义服务器设置、Provider 连接、模型组和认证。从模板开始：

```bash
cp config/config.example.yml config/config.yml
```

> **安全提示**：`config/config.yml` 包含 API 密钥，已加入 gitignore，切勿提交到仓库。

### 2.1 完整示例

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
    headers:
      X-Custom-Header: "custom-value"

model_groups:
  smart:
    - "openai/gpt-4o"
    - "openai/gpt-4o-mini"

log_level: "INFO"
```

### 2.2 字段说明

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `host` | 字符串 | `"0.0.0.0"` | 否 | 服务器绑定地址。 |
| `port` | 整型 | `8000` | 否 | 服务器端口。 |
| `api_key` | 字符串 | — | 是 | 用于认证传入请求的 API 密钥。仅允许字母、数字、下划线和连字符（`^[a-zA-Z0-9_-]+$`）。 |
| `default_preset` | 字符串 | — | 是 | 默认流水线预设名称。必须与 `config/presets/` 下某个 `.yaml` 文件名（不含扩展名）匹配。 |
| `providers` | 字典 | `{}` | 否 | Provider 定义（见下方）。 |
| `model_groups` | 字典 | `{}` | 否 | 模型回退组（见下方）。 |
| `log_level` | 字符串 | `"INFO"` | 否 | 日志级别：`TRACE`、`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`。 |

### 2.3 Provider 配置

每个 Provider 是一个 OpenAI 兼容的 API 后端：

```yaml
providers:
  my-provider:
    base_url: "https://api.example.com/v1"
    api_key: "key-here"
    headers:
      X-Custom-Header: "custom-value"
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `base_url` | 字符串 | 是 | API 基地址。服务器会拼接 `/audio/transcriptions`（STT）和 `/chat/completions`（Chat）。末尾斜杠自动去除。 |
| `api_key` | 字符串 | 是 | 以 `Authorization: Bearer <api_key>` 形式附加在每个请求中。 |
| `headers` | 字典 | 否 | 每次请求该 Provider 时附加的自定义 HTTP 头。与默认头（`Authorization`）合并，自定义头优先级更高。 |

Provider ID（YAML 键名，如 `my-provider`）用于引用模型：`my-provider/whisper-1`。

### 2.4 模型组

模型组定义**回退链** — 如果第一个模型失败，自动尝试下一个：

```yaml
model_groups:
  smart:
    - "openai/gpt-4o"        # 最先尝试
    - "openai/gpt-4o-mini"   # gpt-4o 失败时尝试
    - "local/llama3"          # gpt-4o-mini 也失败时尝试
```

每个条目必须是 `provider_id/model_name` 格式，其中 `provider_id` 必须存在于 `providers` 中。

在预设中通过组名引用（如 `model: "smart"`），而非指定具体模型。系统在 `HTTPStatusError`、`ConnectError` 或 `TimeoutException` 时自动切换到下一个模型。如果全部失败，任务抛出 `AllModelsFailedError`。

### 2.5 环境变量

| 变量 | 值 | 说明 |
|------|-----|------|
| `SKIP_AUTH` | `1`、`true`、`yes` | 跳过 Bearer 令牌认证。默认启用认证。 |

---

## 3. 流水线预设（`config/presets/*.yaml`）

`config/presets/` 下的每个 `.yaml` 文件定义一个流水线预设。文件名（不含 `.yaml`）即为预设名称。API 请求中的 `model` 字段决定使用哪个预设。

> **安全提示**：预设文件可能包含敏感信息，默认已加入 gitignore。

### 3.1 工作原理

一个流水线由有序的 **Block** 组成，每个 Block 包含一个或多个 **Task**。

- **Block 按顺序执行** — Block 2 等 Block 1 完成后才开始。
- **同一个 Block 内的 Task 并行执行** — 同一 Block 的所有 Task 同时启动。
- **后面的 Block 可以引用前面 Block 的结果** — 在 prompt 中使用 `{block_tag.task_tag.result}` 语法。
- **检查点机制** 实现优雅降级 — 如果后续 Block 失败，服务器返回最近一次检查点的结果，而非报错。

### 3.2 完整预设示例

```yaml
# config/presets/default.yaml

output: "{llm.corrected.result}"

blocks:
  # Block 1：语音转文本
  - tag: stt
    name: "转录"
    tasks:
      - tag: raw
        type: transcriptions
        model: "openai/whisper-1"
    checkpoint: "raw"    # 保存此结果作为回退值

  # Block 2：LLM 纠错（使用 Block 1 的结果）
  - tag: llm
    name: "纠错"
    tasks:
      - tag: corrected
        type: chat
        model: "smart"   # 使用模型组回退链
        messages:
          - role: "system"
            content: |
              修正拼写、语法和标点符号错误。
              只返回修正后的文本。
          - role: "user"
            content: |
              原始转录：
              {stt.raw.result}
        max_retries: 2
        model_params:
          temperature: 0.3

      # 示例：将音频发送给 VLLM/VibeVoice 兼容的 provider 的 chat 任务
      # - tag: audio_correction
      #   type: chat
      #   model: "vllm-provider/some-model"
      #   need_audio: true
      #   audio_format: audio_url   # 默认为 "input_audio"（OpenAI 格式）
      #   messages:
      #     - role: "user"
      #       content: "根据音频纠正转录结果。"
```

执行过程：

1. Block `stt` 转录音频，结果作为检查点保存。
2. Block `llm` 将转录结果发送给 LLM 纠错。`{stt.raw.result}` 会被替换为实际的转录文本。
3. 如果 LLM 在重试后仍然失败，服务器返回原始转录结果（检查点回退），而不是报错。
4. `output` 字段决定最终返回哪个 Task 的结果。

### 3.3 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `output` | 字符串 | 是 | 最终返回哪个 Task 的结果。格式：`{block_tag.task_tag.result}`。 |
| `blocks` | 列表 | 是 | 有序的 Block 列表。 |

### 3.4 Block 字段

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `tag` | 字符串 | — | 是 | Block 的唯一标识。 |
| `name` | 字符串 | `null` | 否 | 人类可读的名称（仅用于日志）。 |
| `tasks` | 列表 | — | 是 | 该 Block 内并行执行的 Task 列表。 |
| `checkpoint` | 字符串 | `null` | 否 | 本 Block 内某个 Task 的 tag，其结果作为回退值保存。如果后续 Block 失败，返回此值而非报错。 |

### 3.5 Task 字段

| 字段 | 类型 | 默认值 | 必填 | 说明 |
|------|------|--------|------|------|
| `tag` | 字符串 | — | 是 | Task 在 Block 内的唯一标识。 |
| `type` | 字符串 | — | 是 | `"transcriptions"`（STT）或 `"chat"`（LLM）。 |
| `model` | 字符串 | — | 是 | `"provider_id/model_name"`（直接引用）或模型组名称（回退链）。 |
| `need_audio` | 布尔 | `false` | 否 | 是否向此 Task 发送音频。`transcriptions` 类型始终发送。`chat` 类型设为 `true` 时以 `audio_format` 指定的格式发送音频。 |
| `audio_format` | 字符串 | `"input_audio"` | 否 | `chat` 类型且 `need_audio` 为 true 时的音频内容格式。`"input_audio"`：OpenAI 原生格式（`{"type": "input_audio", "input_audio": {"data": "...", "format": "wav"}}`）。`"audio_url"`：data URI 格式，适用于 VLLM/VibeVoice 等兼容服务（`{"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,..."}}`）。`transcriptions` 类型忽略此字段。 |
| `audio_force_transcode` | 字符串 | `null` | 否 | 在发送给该 Task 前，使用 `ffmpeg` 将音频真实重编码为 `"wav"` 或 `"mp3"`。对 `chat` 类型要求 `need_audio: true`；对 `transcriptions` 类型会同时改变发送给 provider 的音频字节、文件名和 content type。 |
| `prompt` | 字符串 | `null` | 否 | `transcriptions` 类型的提示文本，作为 `prompt` 表单字段发送。支持 `{block.task.result}` 变量替换。对 `chat` 类型无效；请改用 `messages`。 |
| `messages` | 列表 | `null` | 否 | `chat` 类型的消息列表。每个条目包含 `role`、`content`，以及可选的 `require_session`、`missing_strategy`。`content` 支持 `{block.task.result}` 和 `{block.task.history[index]}` 变量替换。发送到 chat completions 之前会先经过 session 感知过滤。除非设置了 `need_audio: true`，否则 `chat` 类型必须提供该字段；`transcriptions` 类型禁止使用。 |
| `record` | 字典 | `null` | 否 | 当前任务的 session history 记录配置。启用后，如果请求使用了 `preset_id/session_id`，任务结果会在执行完成后追加到该任务路径对应的内存 history 中。 |
| `max_retries` | 整型 | `0` | 否 | 所有模型失败后重试整条回退链的次数。`0` = 不重试。 |
| `timeout` | 浮点数 | `null` | 否 | 单次请求超时（秒）。未设置时使用全局 HTTP 客户端超时（connect 10s、read 120s、write 30s）。 |
| `model_params` | 字典 | `null` | 否 | 传递给模型 API 的额外参数（如 `temperature`、`top_p`、`max_tokens`）。`chat` 类型合并到 JSON 请求体，`transcriptions` 类型添加为表单字段。 |

`record` 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `enable` | 布尔 | 是 | 是否开启该任务的 session history 记录。 |
| `max_history_length` | 整型 | 当 `enable: true` 时必填 | 这个任务路径在单个 session 中最多保留多少条记录，必须是正整数。每次追加后都会按这个长度截断更旧的记录。 |

`messages[*]` 中和 session 行为相关的字段：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `role` | 字符串 | — | 消息角色，只能是 `system`、`user`、`assistant`。 |
| `content` | 字符串 | — | 消息文本。支持 `{block.task.result}` 和 `{block.task.history[index]}`。 |
| `require_session` | 布尔 | `false` | 如果为 `true` 且请求没有提供 session ID，只会跳过当前这一条消息，不影响其他消息。 |
| `missing_strategy` | 字符串 | `null` | 只控制当前消息里缺失的 history 引用。`skip` 会删除当前这一条消息。`empty` 会把缺失的 `{block.task.history[index]}` 替换为空字符串，并保留该消息。 |

**Task 类型：**

- **`transcriptions`** — 以 multipart 表单形式将音频 POST 到 `{base_url}/audio/transcriptions`。如果设置了 `audio_force_transcode`，服务器会先用 `ffmpeg` 对上传音频做真实重编码，再发送给下游 provider。返回响应中的 `text` 字段。流式传输禁用（`stream: false`）。
- **`chat`** — 以 JSON POST 到 `{base_url}/chat/completions`，发送 `messages` 数组（可选 base64 音频）。音频内容格式取决于 `audio_format` 设置：`"input_audio"` 使用 OpenAI 原生格式，`"audio_url"` 使用 data URI 格式，兼容 VLLM/VibeVoice。如果设置了 `audio_force_transcode`，服务器会先用 `ffmpeg` 对音频做真实重编码，再把匹配的新格式元数据写入请求。音频会附加到最后一个 `user` 消息；如果没有 `user` 消息，则会在末尾补一个仅包含音频内容的 `user` 消息。返回 `choices[0].message.content`。流式传输禁用（`stream: false`）。

> **运行时要求**：`audio_force_transcode` 依赖系统中的 `ffmpeg` 可执行文件。仓库提供的 Docker 镜像会自动安装它。

### 3.6 变量替换

在 prompt 或消息内容中用 `{block_tag.task_tag.result}` 引用前面 Block 的结果：

```yaml
# transcriptions 任务
prompt: "纠正这段文字：{stt.raw.result}"

# chat 任务
messages:
  - role: "user"
    content: "纠正这段文字：{stt.raw.result}"
```

Chat 消息还可以通过 `{block_tag.task_tag.history[index]}` 读取保留的 session history：

```yaml
output: "{reply.answer.result}"

blocks:
  - tag: stt
    tasks:
      - tag: raw
        type: transcriptions
        model: "openai/whisper-1"
        record:
          enable: true
          max_history_length: 3

  - tag: reply
    tasks:
      - tag: answer
        type: chat
        model: "openai/gpt-4o-mini"
        messages:
          - role: "system"
            content: "你正在继续一个已有的转写校对 session。"
            require_session: true
            missing_strategy: skip
          - role: "user"
            content: |
              当前转写：
              {stt.raw.result}

              上一次最新转写：
              {stt.raw.history[0]}
            missing_strategy: empty
```

`.history[index]` 的索引语义如下：

- `[0]` 表示当前保留记录中最新的一条
- `[1]` 表示第二新的一条
- `[-1]` 表示当前保留记录中最旧的一条
- 负数索引从保留队尾向前计数，逐步指向更旧的保留记录
- 如果 `max_history_length` 截断了 history，保留的是最新的记录，索引只针对截断后仍保留的那一段

消息过滤和缺失 history 的处理规则是精确的：

- `require_session: true` 在请求没有 session ID 时，只跳过当前这一条消息
- `missing_strategy: skip` 在当前消息引用的某个 `.history[...]` 指向不存在的保留记录时，只移除当前这一条消息
- `missing_strategy: empty` 会保留当前消息，并把缺失的 `.history[...]` 引用替换为空字符串
- 这些规则只作用于 chat `messages[*]`，不作用于顶层 `output`

规则：
- 只能引用**更早** Block 的 Task（不能引用同一个 Block 内的 Task — 它们并行执行）。
- 前向引用在启动时会被拒绝。
- `.history[index]` 只支持在运行时会经过 chat message 组装与解析的消息内容中使用。顶层 `output` 仍然只接受 `{block_tag.task_tag.result}`，并且 v1 中 transcriptions 的 `prompt` 不会解析 `.history[...]` 引用。
- JSON 风格的大括号如 `{"key": "value"}` 不受影响。

---

## 4. 内部实现

本节面向贡献者和高级用户，介绍实现细节。

### 4.1 执行流程

```
run_pipeline(preset, app_config, http_client, audio_bytes)
  │
  ├── 遍历每个 block（顺序执行）:
  │    │
  │    ├── 遍历 block 中的每个 task（asyncio.gather 并行）:
  │    │    ├── resolve_model() → [(ProviderClient, model_name), ...]
  │    │    ├── resolve_variables(messages, results)  [仅 chat]
  │    │    └── _call_task_with_retries()
  │    │         ├── call_with_fallback()
  │    │         │    ├── 遍历 (client, model):
  │    │         │    │    ├── call_fn(client, model)
  │    │         │    │    ├── HTTPStatusError/ConnectError/TimeoutException → 下一个
  │    │         │    │    └── 其他异常 → 立即重新抛出
  │    │         │    └── 全部失败 → AllModelsFailedError
  │    │         └── AllModelsFailedError/ConnectError → 重试或重新抛出
  │    │
  │    ├── 有 task 异常？
  │    │    ├── 有检查点 → PipelineFallback
  │    │    └── 无检查点 → PipelineError
  │    │
  │    └── 设置了 block.checkpoint？
  │         └── 保存结果为 last_checkpoint_value
  │
  └── 返回 ResultStore
```

### 4.2 重试与回退细节

- **重试范围**：`max_retries` 重试的是**整条回退链**，而非单个模型。`max_retries: 2` = 最多 3 次尝试（1 次初始 + 2 次重试）。
- **可重试错误**：`AllModelsFailedError`、`httpx.ConnectError`、`httpx.TimeoutException`。
- **不可重试错误**：其他所有异常立即重新抛出。
- **检查点回退**仅在**前面的** Block 保存了检查点时触发。失败 Block 自身的检查点还不可用。

### 4.3 HTTP 客户端默认值

启动时创建一个共享的 `httpx.AsyncClient`，默认值如下：

| 配置项 | 值 |
|--------|-----|
| `max_connections` | 100 |
| `max_keepalive_connections` | 20 |
| `connect` 超时 | 10s |
| `read` 超时 | 120s |
| `write` 超时 | 30s |
| `pool` 超时 | 15s |

Task 未设置 `timeout` 时，使用上述全局超时。

### 4.4 旧版配置回退

如果 `config/config.yml` 不存在，加载器会尝试读取 `config/app.yaml` 作为向后兼容的备选路径。若 `config.yml` 存在则忽略 `app.yaml`。

### 4.5 校验规则

所有校验在启动时执行，任何规则不通过则服务器拒绝启动。

**应用配置：**

| 规则 | 详情 |
|------|------|
| `api_key` 格式 | `^[a-zA-Z0-9_-]+$` |
| `model_groups` 条目 | 每个条目必须包含 `/`。 |
| `log_level` | 必须为 `TRACE`、`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` 之一。 |
| `default_preset` | 必须匹配 `config/presets/` 下的某个 `.yaml` 文件。 |
| 模型引用 | 模型组和 Task 中的每个 `provider_id` 都必须存在于 `providers` 中。 |

**流水线配置：**

| 规则 | 详情 |
|------|------|
| Block tag 唯一 | 整个流水线内不重复。 |
| Task tag 唯一 | 同一 Block 内不重复。 |
| `output` 格式 | 必须为 `{block_tag.task_tag.result}`。 |
| `output` 引用 | 必须指向流水线中实际存在的 Task。 |
| 变量引用 | 只能引用更早 Block 的 Task。同 Block 和前向引用会被拒绝。 |
| `checkpoint` | 必须匹配同一 Block 内某个 Task 的 tag。 |
