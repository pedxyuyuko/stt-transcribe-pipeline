[English](README.md) | 中文

# stt-transcribe-pipeline

可配置的多阶段 STT 转写流水线。FastAPI 服务器接收音频，运行可配置的基于 Block 的流水线（STT + LLM 纠错），通过 OpenAI 兼容的 Provider API 调用模型，返回纠正后的文本。

## 概述

stt-transcribe-pipeline 是一个为音频转写设计的高级流水线引擎。它不依赖单一的 STT 模型，而是将转写过程组织为多个 Block，每个 Block 内包含若干个并行执行的 Task。Block 之间按顺序执行，前一 Block 的结果可以通过变量引用传递给后续 Block，从而实现多模型交叉校对、LLM 纠错等复杂工作流。

服务器 API 兼容 OpenAI 的 `/v1/audio/transcriptions` 接口格式，可以直接对接现有的 OpenAI 客户端工具。

## 特性

- **多阶段流水线**：将转写流程拆分为多个 Block，Block 间串行、Block 内 Task 并行
- **多 Provider 支持**：任意 OpenAI 兼容 API 均可作为 Provider 接入
- **模型组回退链**：配置模型组后自动按顺序尝试，首个可用模型响应即可
- **跨模型纠错**：多个 STT 模型并行转写，LLM 对比结果生成最终文本
- **变量引用**：`{block_tag.task_tag.result}` 语法，前序 Block 结果可直接注入后续 Prompt
- **重试机制**：按 Task 级别配置重试次数，自动应对偶发网络故障
- **灵活预设**：YAML 定义的 Pipeline Preset，按需切换不同工作流
- **自定义模型参数**：通过 `model_params` 为每个 Task 独立设置 `temperature`、`top_p`、`thinking` 等任意模型参数
- **OpenAI 兼容 API**：兼容现有的 OpenAI 客户端和工具链
- **Bearer Token 认证**：设置 `SKIP_AUTH=1` 环境变量可跳过认证，便于本地开发

## 快速开始

### Docker Compose（推荐）

1. 创建工作目录并下载所需文件：

```bash
mkdir -p stt-transcribe-pipeline/config/presets && cd stt-transcribe-pipeline

# 下载 docker-compose.yml 和配置模板
curl -fsSLO https://raw.githubusercontent.com/uuz233/stt-transcribe-pipeline/master/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/uuz233/stt-transcribe-pipeline/master/config/config.example.yml -o config/config.yml
```

2. 编辑 `config/config.yml` 填入你的 Provider 地址和 API Key，并在 `config/presets/` 下创建流水线预设文件（参考 [配置指南](docs/configuration.zh-CN.md)）。

3. 启动服务：

```bash
docker compose up -d
```

### 手动安装

需要 Python 3.10 或更高版本。

```bash
# 克隆项目
git clone https://github.com/uuz233/stt-transcribe-pipeline.git
cd stt-transcribe-pipeline

# 安装依赖
pip install -e .

# 准备配置文件
cp config/config.example.yml config/config.yml
# 编辑 config/config.yml，并在 config/presets/ 下创建预设文件

# 启动服务
python main.py
```

服务默认监听 `0.0.0.0:8000`。地址和端口可通过 `config/config.yml` 中的 `host` 和 `port` 字段修改。

## 使用方法

服务启动后，通过 POST 请求 `/v1/audio/transcriptions` 提交音频文件。

### 默认预设

使用 `default_preset` 配置的预设：

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -F "file=@recording.wav" \
  -F "model=openai/whisper-1" \
  -F "response_format=json"
```

### 指定预设

通过 `model` 字段指定要使用的预设名称（对应 `config/presets/` 下的文件名，不含 `.yaml` 扩展名）：

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-your-api-key-here" \
  -F "file=@recording.wav" \
  -F "model=my-preset"
```

如果 `model` 为空或不匹配任何预设，则回退到 `default_preset`。

### 健康检查

```bash
curl http://localhost:8000/health
```

### 响应格式

`response_format` 参数支持三种模式：

- `json`（默认）：返回 OpenAI 风格 JSON，包含 `text` 字段
- `text`：返回纯文本
- `verbose_json`：返回完整的中间结果，包括流水线中每个 Block 和 Task 的中间输出，便于调试和分析

### 认证

默认情况下需要 Bearer Token 认证。Token 在 `config/config.yml` 的 `api_key` 字段中配置。

开发调试时可设置环境变量跳过认证：

```bash
SKIP_AUTH=1 python main.py
```

## 配置

配置分为两层：

- **应用配置**（`config/config.yml`）：定义 Provider 连接信息、模型组、API Key、日志级别。基于 `config/config.example.yml` 模板创建
- **流水线预设**（`config/presets/*.yaml`）：定义 Block 结构、Task 类型、Prompt 模板、重试策略。每个 YAML 文件是一个独立的 Preset

模型引用格式为 `provider_id/model_name`（直接指定）或模型组名称（自动回退链）。例如 `openai/whisper-1` 或 `smart`。

变量引用使用 `{block_tag.task_tag.result}` 语法，只能引用前面已执行 Block 的结果，同一 Block 内的 Task 并行执行、结果不可互相引用。

音频文件最大支持 25MB。

完整配置参考请查看 [配置指南](docs/configuration.zh-CN.md)。

## 工作原理

### 执行模型

流水线的执行遵循固定结构：

1. **Block 按顺序执行**：第一个 Block 完成后才开始第二个
2. **Block 内的 Task 并行执行**：同一 Block 中的多个 Task 通过 `asyncio.gather` 同时发起
3. **结果存储与引用**：每个 Task 完成后结果存入运行上下文，后续 Block 可通过变量引用访问

举个例子，一个典型的纠错流水线：

```
Block 1 [stt]          Block 2 [correct]
  +- qwen (STT)     ---->
  +- whisper (STT)  ---->  final (LLM: 对比两份结果输出最终文本)
```

第一 Block 中 Qwen ASR 和 Whisper 并行转写同一段音频。第二 Block 的 LLM 通过 `{stt.qwen.result}` 和 `{stt.whisper.result}` 获取两份转写结果，进行交叉对比，生成纠错后的最终文本。

### 失败回退

- **重试**：配置 `max_retries` 后，失败的 Task 自动重试。可捕获 `HTTPStatusError`、`ConnectError` 和 `TimeoutException`
- **模型组**：当使用模型组名称而非具体模型时，系统按列表顺序依次尝试，直到某个模型成功响应
- **检查点**：Block 可以设置 `checkpoint`，保存该 Block 的结果。如果后续 Block 失败，流水线返回最近一个检查点的结果，而不是报错

### 变量解析

变量引用格式为 `{block_tag.task_tag.result}`，解析引擎在 Task 执行前将变量替换为实际值。同一 Block 内的 Task 不能使用变量互相引用，因为它们并行执行、结果尚未就绪。

## 项目结构

```
stt-transcribe-pipeline/
├── main.py                  # FastAPI 应用入口、生命周期管理、认证中间件
├── Dockerfile               # 多阶段构建（python:3.12-slim）
├── docker-compose.yml       # Docker Compose 配置（Docker Hub 预构建镜像）
├── app/
│   ├── api/
│   │   ├── transcription.py # POST /v1/audio/transcriptions 端点
│   │   └── health.py        # GET /health 端点
│   ├── config/
│   │   ├── schema.py        # Pydantic 配置模型定义
│   │   └── loader.py        # YAML 配置加载与跨实体引用校验
│   ├── engine/
│   │   ├── pipeline.py      # 流水线编排器：Block 调度、Task 并行、重试逻辑
│   │   └── resolver.py      # 变量解析器：{block.task.result} 替换
│   └── services/
│       ├── providers.py     # Provider 客户端、模型解析、回退链调用
│       ├── stt.py           # STT Task 执行：multipart POST 到 /audio/transcriptions
│       └── llm.py           # Chat Task 执行：JSON POST 到 /chat/completions
├── config/
│   ├── config.example.yml   # 应用配置模板
│   └── presets/             # 流水线预设目录
├── docs/
│   ├── configuration.en.md  # 配置参考文档（英文）
│   └── configuration.zh-CN.md # 配置参考文档（中文）
├── tests/                   # pytest-asyncio + pytest-httpx 测试集
└── pyproject.toml           # 项目元数据和依赖
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 运行特定测试
pytest tests/test_pipeline.py -v
```

测试中通过 `conftest.py` 设置 `SKIP_AUTH=1` 跳过认证。HTTP 请求使用 `pytest-httpx` 进行 Mock。

### 添加新的 Task 类型

1. 在 `app/config/schema.py` 的 `TaskConfig.type` 中添加新的 Literal 选项
2. 在 `app/engine/pipeline.py` 中添加对应的 `elif` 处理分支

### Preset 开发

在 `config/presets/` 目录下创建新的 YAML 文件，每个文件对应一个独立的 Preset。客户端通过请求的 `model` 字段指定预设文件名来选择对应的 Preset。

## 许可证

MIT License

版权所有 (c) 2026 路过的幽幽子
