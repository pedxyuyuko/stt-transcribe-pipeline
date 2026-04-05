import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from app.config.schema import PipelineConfig, AppConfig, ProviderConfig
from app.engine.pipeline import run_pipeline, get_pipeline_output, PipelineError
from app.engine.resolver import VariableNotFoundError
from app.services.providers import AllModelsFailedError, resolve_model


@pytest.fixture
def simple_pipeline_config():
    return PipelineConfig(
        output="{stt.qwen.result}",
        blocks=[
            {
                "tag": "stt",
                "tasks": [
                    {
                        "tag": "qwen",
                        "type": "transcriptions",
                        "model": "openai/gpt-4o",
                        "need_audio": True,
                    }
                ],
            }
        ],
    )


@pytest.fixture
def two_block_pipeline_config():
    return PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            {
                "tag": "stt",
                "tasks": [
                    {
                        "tag": "qwen",
                        "type": "transcriptions",
                        "model": "openai/gpt-4o",
                        "need_audio": True,
                    }
                ],
            },
            {
                "tag": "correct",
                "tasks": [
                    {
                        "tag": "final",
                        "type": "chat",
                        "model": "smart",
                        "need_audio": True,
                        "prompt": "Fix {stt.qwen.result}",
                    }
                ],
            },
        ],
    )


@pytest.fixture
def app_config():
    return AppConfig(
        host="0.0.0.0",
        port=8000,
        api_key="sk-test",
        default_preset="default",
        providers={
            "openai": {"base_url": "http://localhost:8080/v1", "api_key": "test"}
        },
        model_groups={},
    )


@pytest.fixture
def app_config_with_group():
    return AppConfig(
        host="0.0.0.0",
        port=8000,
        api_key="sk-test",
        default_preset="default",
        providers={
            "openai": {"base_url": "http://localhost:8080/v1", "api_key": "test"},
            "backup": {"base_url": "http://localhost:8081/v1", "api_key": "test"},
        },
        model_groups={"smart": ["openai/gpt-4o", "backup/gpt-4o-mini"]},
    )


@pytest.mark.asyncio
async def test_single_block_single_task(simple_pipeline_config, app_config, httpx_mock):
    import httpx

    httpx_mock.add_response(
        url="http://localhost:8080/v1/audio/transcriptions",
        json={"text": "hello world"},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=simple_pipeline_config,
            models_config=app_config,
            client=client,
            audio_bytes=b"fake audio",
        )

    assert results == {"stt.qwen": "hello world"}


@pytest.mark.asyncio
async def test_two_blocks_variable_passing(
    two_block_pipeline_config, app_config_with_group, httpx_mock
):
    import httpx

    httpx_mock.add_response(
        url="http://localhost:8080/v1/audio/transcriptions",
        json={"text": "hello"},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "hello world"}}]},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=two_block_pipeline_config,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
        )

    assert results["stt.qwen"] == "hello"
    assert results["correct.final"] == "hello world"


def test_get_pipeline_output():
    results = {"stt.qwen": "hello", "correct.final": "world"}
    output = get_pipeline_output("{correct.final.result}", results)
    assert output == "world"


def test_get_pipeline_output_missing_variable():
    with pytest.raises(VariableNotFoundError):
        get_pipeline_output("{nonexistent.task.result}", {})


@pytest.mark.asyncio
async def test_all_models_failed(app_config, httpx_mock):
    import httpx
    from app.config.schema import PipelineConfig

    pipeline = PipelineConfig(
        output="{stt.qwen.result}",
        blocks=[
            {
                "tag": "stt",
                "tasks": [
                    {
                        "tag": "qwen",
                        "type": "transcriptions",
                        "model": "openai/gpt-4o",
                        "need_audio": True,
                    }
                ],
            }
        ],
    )

    httpx_mock.add_exception(
        httpx.ConnectError("connection failed"),
        url="http://localhost:8080/v1/audio/transcriptions",
        method="POST",
    )

    with pytest.raises(PipelineError) as exc_info:
        async with httpx.AsyncClient() as client:
            await run_pipeline(
                preset=pipeline,
                models_config=app_config,
                client=client,
                audio_bytes=b"fake audio",
            )

    assert exc_info.value.block_tag == "stt"
    assert exc_info.value.task_tag == "qwen"
