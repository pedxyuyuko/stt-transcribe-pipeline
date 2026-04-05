import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def sample_models_config():
    from app.config.schema import AppConfig

    return AppConfig(
        host="0.0.0.0",
        port=8000,
        api_key="sk-test",
        default_preset="default",
        providers={
            "openai": {"base_url": "http://localhost:8080/v1", "api_key": "test"},
            "local": {"base_url": "http://localhost:8000/v1", "api_key": "none"},
        },
        model_groups={
            "smart": ["openai/gpt-4o", "openai/gpt-4o-mini"],
        },
    )


@pytest.fixture
def sample_pipeline_config():
    from app.config.schema import PipelineConfig

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
