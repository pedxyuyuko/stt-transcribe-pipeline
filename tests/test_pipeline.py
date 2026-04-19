import pytest
from unittest.mock import AsyncMock

from app.config.schema import (
    AppConfig,
    BlockConfig,
    MessageConfig,
    PipelineConfig,
    ProviderConfig,
    TaskConfig,
)
from app.engine.pipeline import run_pipeline, get_pipeline_output, PipelineError
from app.engine.resolver import VariableNotFoundError
from app.services.providers import ProviderClient


def _extract_input_audio_format(messages: list[dict[str, object]]) -> str:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "input_audio":
                input_audio = part.get("input_audio")
                if isinstance(input_audio, dict):
                    audio_format = input_audio.get("format")
                    if isinstance(audio_format, str):
                        return audio_format
    raise AssertionError("No input_audio content part found")


def _extract_audio_url(messages: list[dict[str, object]]) -> str:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "audio_url":
                audio_url = part.get("audio_url")
                if isinstance(audio_url, dict):
                    url = audio_url.get("url")
                    if isinstance(url, str):
                        return url
    raise AssertionError("No audio_url content part found")


@pytest.fixture
def simple_pipeline_config():
    return PipelineConfig(
        output="{stt.qwen.result}",
        blocks=[
            BlockConfig(
                tag="stt",
                tasks=[
                    TaskConfig(
                        tag="qwen",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                    )
                ],
            )
        ],
    )


@pytest.fixture
def two_block_pipeline_config():
    return PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="stt",
                tasks=[
                    TaskConfig(
                        tag="qwen",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                    )
                ],
            ),
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="smart",
                        need_audio=True,
                        messages=[
                            MessageConfig(role="user", content="Fix {stt.qwen.result}")
                        ],
                    )
                ],
            ),
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
            "openai": ProviderConfig(
                base_url="http://localhost:8080/v1", api_key="test"
            )
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
            "openai": ProviderConfig(
                base_url="http://localhost:8080/v1", api_key="test"
            ),
            "backup": ProviderConfig(
                base_url="http://localhost:8081/v1", api_key="test"
            ),
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


@pytest.mark.asyncio
async def test_chat_task_without_messages_uses_audio_only_payload(
    app_config_with_group, httpx_mock
):
    import httpx

    pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="smart",
                        need_audio=True,
                    )
                ],
            )
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "audio only ok"}}]},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
        )

    assert results["correct.final"] == "audio only ok"
    request = httpx_mock.get_requests()[0]
    payload = request.read().decode("utf-8")
    assert '"messages":[{"role":"user","content":[{"type":"input_audio"' in payload


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
            BlockConfig(
                tag="stt",
                tasks=[
                    TaskConfig(
                        tag="qwen",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                    )
                ],
            )
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


@pytest.mark.asyncio
async def test_chat_audio_appends_to_last_user_message():
    from app.services.llm import execute_chat_task

    task = TaskConfig(
        tag="final",
        type="chat",
        model="smart",
        need_audio=True,
        messages=[
            MessageConfig(role="system", content="system instruction"),
            MessageConfig(role="user", content="first user turn"),
            MessageConfig(role="assistant", content="assistant turn"),
            MessageConfig(role="user", content="final user turn"),
        ],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]

    import httpx

    async with httpx.AsyncClient() as client:
        result = await execute_chat_task(
            provider_client=provider_client,
            task=task,
            resolved_messages=[
                {"role": "system", "content": "system instruction"},
                {"role": "user", "content": "first user turn"},
                {"role": "assistant", "content": "assistant turn"},
                {"role": "user", "content": "final user turn"},
            ],
            audio_bytes=b"fake audio",
            audio_input_format="mp3",
            client=client,
            model_name="gpt-4o",
        )

    assert result == "ok"
    assert provider_client.post_chat.await_args is not None
    call_kwargs = provider_client.post_chat.await_args.kwargs
    sent_messages = call_kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": "system instruction"}
    assert sent_messages[1] == {"role": "user", "content": "first user turn"}
    assert sent_messages[2] == {"role": "assistant", "content": "assistant turn"}
    assert sent_messages[3]["role"] == "user"
    assert sent_messages[3]["content"][0] == {
        "type": "text",
        "text": "final user turn",
    }
    assert sent_messages[3]["content"][1]["type"] == "input_audio"
    assert sent_messages[3]["content"][1]["input_audio"]["format"] == "mp3"


@pytest.mark.asyncio
async def test_chat_audio_without_user_message_adds_trailing_user_message():
    from app.services.llm import execute_chat_task

    task = TaskConfig(
        tag="final",
        type="chat",
        model="smart",
        need_audio=True,
        messages=[MessageConfig(role="system", content="system instruction")],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]

    import httpx

    async with httpx.AsyncClient() as client:
        result = await execute_chat_task(
            provider_client=provider_client,
            task=task,
            resolved_messages=[{"role": "system", "content": "system instruction"}],
            audio_bytes=b"fake audio",
            audio_input_format="m4a",
            client=client,
            model_name="gpt-4o",
        )

    assert result == "ok"
    assert provider_client.post_chat.await_args is not None
    call_kwargs = provider_client.post_chat.await_args.kwargs
    sent_messages = call_kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": "system instruction"}
    assert sent_messages[-1]["role"] == "user"
    assert sent_messages[-1]["content"][0]["type"] == "input_audio"
    assert sent_messages[-1]["content"][0]["input_audio"]["format"] == "m4a"


@pytest.mark.asyncio
async def test_chat_audio_url_uses_matching_mime_type():
    from app.services.llm import execute_chat_task

    task = TaskConfig(
        tag="final",
        type="chat",
        model="smart",
        need_audio=True,
        audio_format="audio_url",
        messages=[MessageConfig(role="user", content="system instruction")],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]

    import httpx

    async with httpx.AsyncClient() as client:
        result = await execute_chat_task(
            provider_client=provider_client,
            task=task,
            resolved_messages=[{"role": "user", "content": "system instruction"}],
            audio_bytes=b"fake audio",
            audio_input_format="mp3",
            client=client,
            model_name="gpt-4o",
        )

    assert result == "ok"
    assert provider_client.post_chat.await_args is not None
    call_kwargs = provider_client.post_chat.await_args.kwargs
    sent_messages = call_kwargs["messages"]
    assert _extract_audio_url(sent_messages).startswith("data:audio/mpeg;base64,")


@pytest.mark.asyncio
async def test_run_pipeline_propagates_audio_input_format_to_chat(
    app_config_with_group, httpx_mock
):
    import json
    import httpx

    pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="smart",
                        need_audio=True,
                        messages=[MessageConfig(role="user", content="process this")],
                    )
                ],
            )
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            audio_input_format="m4a",
        )

    assert results["correct.final"] == "ok"
    request = httpx_mock.get_requests()[0]
    payload = json.loads(request.read().decode("utf-8"))
    assert _extract_input_audio_format(payload["messages"]) == "m4a"


@pytest.mark.asyncio
async def test_run_pipeline_transcodes_chat_audio_when_requested(
    app_config_with_group, httpx_mock, monkeypatch
):
    import json
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="smart",
                        need_audio=True,
                        audio_force_transcode="wav",
                        messages=[MessageConfig(role="user", content="process this")],
                    )
                ],
            )
        ],
    )

    transcode_mock = AsyncMock(return_value=b"transcoded wav bytes")
    monkeypatch.setattr(pipeline_module, "transcode_audio", transcode_mock)

    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"source bytes",
            audio_input_format="m4a",
        )

    assert results["correct.final"] == "ok"
    transcode_mock.assert_awaited_once_with(
        audio_bytes=b"source bytes",
        source_format="m4a",
        target_format="wav",
    )
    request = httpx_mock.get_requests()[0]
    payload = json.loads(request.read().decode("utf-8"))
    assert _extract_input_audio_format(payload["messages"]) == "wav"


@pytest.mark.asyncio
async def test_run_pipeline_transcodes_stt_audio_when_requested(
    simple_pipeline_config, app_config, httpx_mock, monkeypatch
):
    import httpx
    import app.engine.pipeline as pipeline_module

    simple_pipeline_config.blocks[0].tasks[0].audio_force_transcode = "mp3"

    transcode_mock = AsyncMock(return_value=b"transcoded mp3 bytes")
    monkeypatch.setattr(pipeline_module, "transcode_audio", transcode_mock)

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
            audio_bytes=b"source bytes",
            audio_input_format="wav",
        )

    assert results == {"stt.qwen": "hello world"}
    transcode_mock.assert_awaited_once_with(
        audio_bytes=b"source bytes",
        source_format="wav",
        target_format="mp3",
    )
    request = httpx_mock.get_requests()[0]
    body = request.read().decode("utf-8", errors="replace")
    content_type = request.headers["Content-Type"]
    assert 'filename="audio.mp3"' in body
    assert "audio/mpeg" in body
    assert "multipart/form-data" in content_type
