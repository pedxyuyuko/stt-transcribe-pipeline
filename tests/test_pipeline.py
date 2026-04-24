import asyncio
import json
from typing import cast
from collections import deque

import pytest
from unittest.mock import AsyncMock, Mock
import httpx

from app.config.schema import (
    AppConfig,
    BlockConfig,
    MessageConfig,
    PipelineConfig,
    ProviderConfig,
    RecordConfig,
    TaskConfig,
)
from app.engine.pipeline import (
    run_pipeline,
    get_pipeline_output,
    PipelineError,
    PipelineFallback,
)
from app.history_store import SessionHistoryStore
from app.engine.resolver import (
    HistoryEntryNotFoundError,
    SessionRequiredError,
    VariableNotFoundError,
)
from app.services.providers import ProviderClient


class _SimpleHTTPXMock:
    def __init__(self) -> None:
        self._responses: deque[object] = deque()
        self._requests: list[httpx.Request] = []

    def add_response(
        self,
        *,
        url: str,
        method: str,
        json: object | None = None,
        status_code: int = 200,
    ) -> None:
        self._responses.append(
            {
                "kind": "response",
                "url": url,
                "method": method.upper(),
                "json": json,
                "status_code": status_code,
            }
        )

    def add_exception(self, exception: Exception, *, url: str, method: str) -> None:
        self._responses.append(
            {
                "kind": "exception",
                "url": url,
                "method": method.upper(),
                "exception": exception,
            }
        )

    def get_requests(self) -> list[httpx.Request]:
        return list(self._requests)

    def _next_entry(self, method: str, url: str) -> dict[str, object]:
        if not self._responses:
            raise AssertionError(f"Unexpected HTTPX request with no mock left: {method} {url}")

        entry = self._responses.popleft()
        assert isinstance(entry, dict)
        expected_method = entry["method"]
        expected_url = entry["url"]
        if expected_method != method.upper() or expected_url != url:
            raise AssertionError(
                "Unexpected HTTPX request order: "
                + f"got {method} {url}, expected {expected_method} {expected_url}"
            )
        return entry


@pytest.fixture
def httpx_mock(monkeypatch):
    mock = _SimpleHTTPXMock()

    async def _mocked_post(self, url, *args, **kwargs):
        request_kwargs: dict[str, object] = {}
        if "headers" in kwargs and kwargs["headers"] is not None:
            request_kwargs["headers"] = kwargs["headers"]

        if "json" in kwargs and kwargs["json"] is not None:
            request_kwargs["content"] = json.dumps(kwargs["json"]).encode("utf-8")
        if "data" in kwargs and kwargs["data"] is not None:
            request_kwargs["data"] = kwargs["data"]
        if "files" in kwargs and kwargs["files"] is not None:
            request_kwargs["files"] = kwargs["files"]

        request = self.build_request("POST", url, **request_kwargs)
        mock._requests.append(request)
        entry = mock._next_entry(request.method, str(request.url))
        if entry["kind"] == "exception":
            exception = entry["exception"]
            assert isinstance(exception, Exception)
            raise exception
        status_code = entry["status_code"]
        payload = entry["json"]
        assert isinstance(status_code, int)
        return httpx.Response(status_code=status_code, json=payload, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", _mocked_post)
    yield mock
    if mock._responses:
        raise AssertionError(f"Unused HTTPX mock entries remain: {len(mock._responses)}")


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
    payload = json.loads(request.read().decode("utf-8"))
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": "ZmFrZSBhdWRpbw==", "format": "wav"},
                }
            ],
        }
    ]


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
async def test_run_pipeline_binds_distinct_stt_audio_payloads_per_task(
    app_config, monkeypatch
):
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
        output="{stt.raw.result}",
        blocks=[
            BlockConfig(
                tag="stt",
                tasks=[
                    TaskConfig(
                        tag="raw",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                    ),
                    TaskConfig(
                        tag="mp3_copy",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                        audio_force_transcode="mp3",
                    ),
                ],
            )
        ],
    )

    transcode_mock = AsyncMock(return_value=b"transcoded mp3 bytes")
    execute_stt_mock = AsyncMock(side_effect=["raw text", "mp3 text"])
    monkeypatch.setattr(pipeline_module, "transcode_audio", transcode_mock)
    monkeypatch.setattr(pipeline_module, "execute_stt_task", execute_stt_mock)

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config,
            client=client,
            audio_bytes=b"source bytes",
            audio_filename="clip.wav",
            audio_input_format="wav",
        )

    assert results == {"stt.raw": "raw text", "stt.mp3_copy": "mp3 text"}
    transcode_mock.assert_awaited_once_with(
        audio_bytes=b"source bytes",
        source_format="wav",
        target_format="mp3",
    )
    assert execute_stt_mock.await_count == 2

    first_call = execute_stt_mock.await_args_list[0].kwargs
    second_call = execute_stt_mock.await_args_list[1].kwargs
    assert first_call["task"].tag == "raw"
    assert first_call["audio_bytes"] == b"source bytes"
    assert first_call["filename"] == "clip.wav"
    assert first_call["content_type"] == "audio/wav"
    assert second_call["task"].tag == "mp3_copy"
    assert second_call["audio_bytes"] == b"transcoded mp3 bytes"
    assert second_call["filename"] == "audio.mp3"
    assert second_call["content_type"] == "audio/mpeg"


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


@pytest.mark.asyncio
async def test_history_store_read_append_truncate():
    store = SessionHistoryStore(max_history_length=3)

    assert await store.read("session-a", "block.task") == []

    assert await store.append("session-a", "block.task", "first") == ["first"]
    assert await store.append("session-a", "block.task", "second") == [
        "second",
        "first",
    ]
    assert await store.append("session-a", "block.task", "third") == [
        "third",
        "second",
        "first",
    ]
    assert await store.append("session-a", "block.task", "fourth") == [
        "fourth",
        "third",
        "second",
    ]

    assert await store.read("session-a", "block.task") == [
        "fourth",
        "third",
        "second",
    ]
    assert await store.truncate("session-a", "block.task", max_history_length=2) == [
        "fourth",
        "third",
    ]
    assert await store.read("session-a", "block.task") == ["fourth", "third"]


@pytest.mark.asyncio
async def test_history_store_logs_per_key_state(monkeypatch):
    store = SessionHistoryStore(max_history_length=3)

    import app.history_store as history_store_module

    debug_mock = Mock()

    def _debug(*args, **kwargs):
        debug_mock(*args, **kwargs)

    monkeypatch.setattr(history_store_module.logger, "debug", _debug)

    assert await store.read("session-a", "block.task") == []
    assert await store.append("session-a", "block.task", "first") == ["first"]
    assert await store.append("session-a", "block.task", "second") == ["second", "first"]
    assert await store.truncate("session-a", "block.task", max_history_length=1) == ["second"]

    calls = debug_mock.call_args_list
    assert len(calls) == 4

    assert calls[0].args == (
        "Session history read | user_session_id={} | task_path={} | retained_history=[] | retained_length=0",
        "session-a",
        "block.task",
    )
    assert calls[1].args == (
        "Session history append | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
        "session-a",
        "block.task",
        ["first"],
        1,
    )
    assert calls[2].args == (
        "Session history append | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
        "session-a",
        "block.task",
        ["second", "first"],
        2,
    )
    assert calls[3].args == (
        "Session history truncate | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
        "session-a",
        "block.task",
        ["second"],
        1,
    )


@pytest.mark.asyncio
async def test_history_store_max_history_length_keeps_newest_results():
    store = SessionHistoryStore(max_history_length=2)

    _ = await store.append("session-a", "block.task", "one")
    _ = await store.append("session-a", "block.task", "two")
    _ = await store.append("session-a", "block.task", "three")

    assert await store.read("session-a", "block.task") == ["three", "two"]


@pytest.mark.asyncio
async def test_history_store_session_isolation():
    store = SessionHistoryStore(max_history_length=3)

    _ = await store.append("session-a", "block.task", "a-1")
    _ = await store.append("session-b", "block.task", "b-1")
    _ = await store.append("session-a", "other.task", "a-other")

    assert await store.read("session-a", "block.task") == ["a-1"]
    assert await store.read("session-b", "block.task") == ["b-1"]
    assert await store.read("session-a", "other.task") == ["a-other"]


@pytest.mark.asyncio
async def test_concurrent_history_store_appends_preserve_bounded_length():
    store = SessionHistoryStore(max_history_length=5)

    async def _append_value(index: int) -> None:
        _ = await store.append("session-a", "block.task", f"value-{index}")

    await asyncio.gather(*[_append_value(index) for index in range(20)])

    history = await store.read("session-a", "block.task")
    assert len(history) == 5
    assert history == [f"value-{index}" for index in range(19, 14, -1)]


@pytest.mark.asyncio
async def test_main_lifespan_attaches_session_history_store():
    from main import app

    async with app.router.lifespan_context(app):
        history_store = cast(SessionHistoryStore, app.state.session_history_store)
        assert isinstance(history_store, SessionHistoryStore)
        assert await history_store.read("session-a", "block.task") == []


@pytest.mark.asyncio
async def test_chat_messages_require_session_skips_only_gated_messages(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
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
                        messages=[
                            MessageConfig(role="system", content="always keep"),
                            MessageConfig(
                                role="user",
                                content="session-only",
                                require_session=True,
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [(provider_client, "gpt-4o")],
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            session_history_store=SessionHistoryStore(),
        )

    assert results["correct.final"] == "ok"
    assert provider_client.post_chat.await_args is not None
    sent_messages = provider_client.post_chat.await_args.kwargs["messages"]
    assert sent_messages == [{"role": "system", "content": "always keep"}]


@pytest.mark.asyncio
async def test_chat_messages_require_session_passes_when_session_exists(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
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
                        messages=[
                            MessageConfig(role="system", content="always keep"),
                            MessageConfig(
                                role="user",
                                content="session-only",
                                require_session=True,
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [(provider_client, "gpt-4o")],
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=SessionHistoryStore(),
        )

    assert results["correct.final"] == "ok"
    assert provider_client.post_chat.await_args is not None
    sent_messages = provider_client.post_chat.await_args.kwargs["messages"]
    assert sent_messages == [
        {"role": "system", "content": "always keep"},
        {"role": "user", "content": "session-only"},
    ]


@pytest.mark.asyncio
async def test_missing_history_skip_removes_only_that_message(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
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
                        messages=[
                            MessageConfig(role="system", content="base {stt.qwen.result}"),
                            MessageConfig(
                                role="user",
                                content="history {stt.qwen.history[0]}",
                                missing_strategy="skip",
                            ),
                        ],
                    )
                ],
            ),
        ],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [
            (provider_client, "whisper-1")
            if model == "openai/gpt-4o"
            else (provider_client, "gpt-4o")
        ],
    )

    history_store = SessionHistoryStore()
    provider_client.post_transcription = AsyncMock(return_value="hello")  # pyright: ignore[reportAttributeAccessIssue]
    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )

    assert results["correct.final"] == "ok"
    assert provider_client.post_chat.await_args is not None
    sent_messages = provider_client.post_chat.await_args.kwargs["messages"]
    assert sent_messages == [{"role": "system", "content": "base hello"}]


@pytest.mark.asyncio
async def test_missing_history_empty_substitutes_empty_string(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
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
                        messages=[
                            MessageConfig(
                                role="user",
                                content="before {stt.qwen.history[0]} after",
                                missing_strategy="empty",
                            )
                        ],
                    )
                ],
            ),
        ],
    )
    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [
            (provider_client, "whisper-1")
            if model == "openai/gpt-4o"
            else (provider_client, "gpt-4o")
        ],
    )

    provider_client.post_transcription = AsyncMock(return_value="hello")  # pyright: ignore[reportAttributeAccessIssue]
    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=SessionHistoryStore(),
        )

    assert results["correct.final"] == "ok"
    assert provider_client.post_chat.await_args is not None
    sent_messages = provider_client.post_chat.await_args.kwargs["messages"]
    assert sent_messages == [{"role": "user", "content": "before  after"}]


@pytest.mark.asyncio
async def test_missing_history_without_strategy_raises_pipeline_error(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
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
                        messages=[
                            MessageConfig(
                                role="user",
                                content="history {stt.qwen.history[0]}",
                            )
                        ],
                    )
                ],
            ),
        ],
    )

    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_transcription = AsyncMock(return_value="hello")  # pyright: ignore[reportAttributeAccessIssue]
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [
            (provider_client, "whisper-1")
            if model == "openai/gpt-4o"
            else (provider_client, "gpt-4o")
        ],
    )

    with pytest.raises(PipelineError) as exc_info:
        async with httpx.AsyncClient() as client:
            await run_pipeline(
                preset=pipeline,
                models_config=app_config_with_group,
                client=client,
                audio_bytes=b"fake audio",
                user_session_id="session-a",
                session_history_store=SessionHistoryStore(),
            )

    assert isinstance(exc_info.value.original_error, HistoryEntryNotFoundError)


@pytest.mark.asyncio
async def test_missing_history_skip_without_session_removes_only_that_message(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
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
                        messages=[
                            MessageConfig(role="system", content="keep me"),
                            MessageConfig(
                                role="user",
                                content="history {stt.qwen.history[0]}",
                                missing_strategy="skip",
                            ),
                        ],
                    )
                ],
            )
        ],
    )

    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_transcription = AsyncMock(return_value="hello")  # pyright: ignore[reportAttributeAccessIssue]
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [
            (provider_client, "whisper-1")
            if model == "openai/gpt-4o"
            else (provider_client, "gpt-4o")
        ],
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            session_history_store=SessionHistoryStore(),
        )

    assert results["correct.final"] == "ok"
    assert provider_client.post_chat.await_args is not None
    sent_messages = provider_client.post_chat.await_args.kwargs["messages"]
    assert sent_messages == [{"role": "system", "content": "keep me"}]


@pytest.mark.asyncio
async def test_missing_history_empty_without_session_substitutes_empty_string(
    app_config_with_group, monkeypatch
):
    from app.services.providers import ProviderClient
    import httpx
    import app.engine.pipeline as pipeline_module

    pipeline = PipelineConfig(
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
                        messages=[
                            MessageConfig(
                                role="user",
                                content="history {stt.qwen.history[0]}",
                                missing_strategy="empty",
                            )
                        ],
                    )
                ],
            )
        ],
    )

    provider_client = ProviderClient(
        base_url="http://localhost:8080/v1",
        api_key="test",
        provider_id="openai",
    )
    provider_client.post_transcription = AsyncMock(return_value="hello")  # pyright: ignore[reportAttributeAccessIssue]
    provider_client.post_chat = AsyncMock(return_value="ok")  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(
        pipeline_module,
        "resolve_model",
        lambda model, config: [
            (provider_client, "whisper-1")
            if model == "openai/gpt-4o"
            else (provider_client, "gpt-4o")
        ],
    )

    async with httpx.AsyncClient() as client:
        results = await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            session_history_store=SessionHistoryStore(),
        )

    assert results["correct.final"] == "ok"
    assert provider_client.post_chat.await_args is not None
    sent_messages = provider_client.post_chat.await_args.kwargs["messages"]
    assert sent_messages == [{"role": "user", "content": "history "}]


@pytest.mark.asyncio
async def test_chat_messages_all_filtered_without_audio_fails_clearly(
    app_config_with_group,
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
                        need_audio=False,
                        messages=[
                            MessageConfig(
                                role="user",
                                content="session-only",
                                require_session=True,
                            )
                        ],
                    )
                ],
            )
        ],
    )

    with pytest.raises(PipelineError) as exc_info:
        async with httpx.AsyncClient() as client:
            await run_pipeline(
                preset=pipeline,
                models_config=app_config_with_group,
                client=client,
                audio_bytes=b"fake audio",
                session_history_store=SessionHistoryStore(),
            )

    assert exc_info.value.block_tag == "correct"
    assert exc_info.value.task_tag == "final"
    assert "has no messages left after session-aware filtering" in str(
        exc_info.value.original_error
    )


@pytest.mark.asyncio
async def test_chat_messages_all_filtered_with_audio_preserves_audio_only_behavior(
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
                        messages=[
                            MessageConfig(
                                role="user",
                                content="session-only",
                                require_session=True,
                            )
                        ],
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
            session_history_store=SessionHistoryStore(),
        )

    assert results["correct.final"] == "audio only ok"
    request = httpx_mock.get_requests()[0]
    payload = json.loads(request.read().decode("utf-8"))
    assert payload["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {"data": "ZmFrZSBhdWRpbw==", "format": "wav"},
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_run_pipeline_records_history_only_after_success(
    app_config_with_group, httpx_mock
):
    import httpx

    history_store = SessionHistoryStore()
    pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="hello")],
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            )
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "first"}}]},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "second"}}]},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "third"}}]},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )
        await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )
        await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )

    assert await history_store.read("session-a", "correct.final") == ["third", "second"]


@pytest.mark.asyncio
async def test_run_pipeline_history_indexes_use_newest_and_oldest_retained_entries(
    app_config_with_group, httpx_mock
):
    import httpx

    history_store = SessionHistoryStore()
    record_pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="hello")],
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            )
        ],
    )
    read_pipeline = PipelineConfig(
        output="{reader.pick.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="current run")],
                    )
                ],
            ),
            BlockConfig(
                tag="reader",
                tasks=[
                    TaskConfig(
                        tag="pick",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[
                            MessageConfig(
                                role="user",
                                content="newest {correct.final.history[0]} oldest {correct.final.history[-1]}",
                            )
                        ],
                    )
                ],
            )
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "first"}}]},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "second"}}]},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "third"}}]},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "resolved indexes"}}]},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        json={"choices": [{"message": {"content": "reader consumed history"}}]},
        method="POST",
    )

    async with httpx.AsyncClient() as client:
        await run_pipeline(
            preset=record_pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )
        await run_pipeline(
            preset=record_pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )
        await run_pipeline(
            preset=record_pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )
        results = await run_pipeline(
            preset=read_pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            user_session_id="session-a",
            session_history_store=history_store,
        )

    assert results["correct.final"] == "resolved indexes"
    assert results["reader.pick"] == "reader consumed history"
    assert await history_store.read("session-a", "correct.final") == ["third", "second"]
    request = httpx_mock.get_requests()[-1]
    payload = json.loads(request.read().decode("utf-8"))
    assert payload["messages"] == [
        {"role": "user", "content": "newest third oldest second"}
    ]


@pytest.mark.asyncio
async def test_run_pipeline_does_not_record_without_user_session(
    app_config_with_group, httpx_mock
):
    import httpx

    history_store = SessionHistoryStore()
    pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="hello")],
                        record=RecordConfig(enable=True, max_history_length=2),
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
        await run_pipeline(
            preset=pipeline,
            models_config=app_config_with_group,
            client=client,
            audio_bytes=b"fake audio",
            session_history_store=history_store,
        )

    assert await history_store.read("session-a", "correct.final") == []


@pytest.mark.asyncio
async def test_failed_task_does_not_record_history(app_config_with_group, httpx_mock):
    import httpx

    history_store = SessionHistoryStore()
    pipeline = PipelineConfig(
        output="{correct.final.result}",
        blocks=[
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="hello")],
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            )
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        status_code=500,
        json={"error": {"message": "boom"}},
        method="POST",
    )

    with pytest.raises(PipelineError):
        async with httpx.AsyncClient() as client:
            await run_pipeline(
                preset=pipeline,
                models_config=app_config_with_group,
                client=client,
                audio_bytes=b"fake audio",
                user_session_id="session-a",
                session_history_store=history_store,
            )

    assert await history_store.read("session-a", "correct.final") == []


@pytest.mark.asyncio
async def test_checkpoint_fallback_does_not_record_failed_later_task(
    app_config_with_group, httpx_mock
):
    import httpx

    history_store = SessionHistoryStore()
    pipeline = PipelineConfig(
        output="{stt.qwen.result}",
        blocks=[
            BlockConfig(
                tag="stt",
                checkpoint="qwen",
                tasks=[
                    TaskConfig(
                        tag="qwen",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            ),
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="hello")],
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            ),
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/audio/transcriptions",
        json={"text": "checkpoint text"},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        status_code=500,
        json={"error": {"message": "boom"}},
        method="POST",
    )

    with pytest.raises(PipelineFallback) as exc_info:
        async with httpx.AsyncClient() as client:
            await run_pipeline(
                preset=pipeline,
                models_config=app_config_with_group,
                client=client,
                audio_bytes=b"fake audio",
                user_session_id="session-a",
                session_history_store=history_store,
            )

    assert exc_info.value.fallback_value == "checkpoint text"
    assert await history_store.read("session-a", "stt.qwen") == ["checkpoint text"]
    assert await history_store.read("session-a", "correct.final") == []


@pytest.mark.asyncio
async def test_checkpoint_fallback_preserves_prior_history_without_appending_failed_value(
    app_config_with_group, httpx_mock
):
    import httpx

    history_store = SessionHistoryStore()
    await history_store.append("session-a", "correct.final", "prior good result")

    pipeline = PipelineConfig(
        output="{stt.qwen.result}",
        blocks=[
            BlockConfig(
                tag="stt",
                checkpoint="qwen",
                tasks=[
                    TaskConfig(
                        tag="qwen",
                        type="transcriptions",
                        model="openai/gpt-4o",
                        need_audio=True,
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            ),
            BlockConfig(
                tag="correct",
                tasks=[
                    TaskConfig(
                        tag="final",
                        type="chat",
                        model="openai/gpt-4o",
                        messages=[MessageConfig(role="user", content="hello")],
                        record=RecordConfig(enable=True, max_history_length=2),
                    )
                ],
            ),
        ],
    )

    httpx_mock.add_response(
        url="http://localhost:8080/v1/audio/transcriptions",
        json={"text": "checkpoint text"},
        method="POST",
    )
    httpx_mock.add_response(
        url="http://localhost:8080/v1/chat/completions",
        status_code=500,
        json={"error": {"message": "boom"}},
        method="POST",
    )

    with pytest.raises(PipelineFallback) as exc_info:
        async with httpx.AsyncClient() as client:
            await run_pipeline(
                preset=pipeline,
                models_config=app_config_with_group,
                client=client,
                audio_bytes=b"fake audio",
                user_session_id="session-a",
                session_history_store=history_store,
            )

    assert exc_info.value.fallback_value == "checkpoint text"
    assert await history_store.read("session-a", "stt.qwen") == ["checkpoint text"]
    assert await history_store.read("session-a", "correct.final") == [
        "prior good result"
    ]
