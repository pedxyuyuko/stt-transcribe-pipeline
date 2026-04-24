import pytest
from types import SimpleNamespace

from app.config.schema import BlockConfig, MessageConfig, TaskConfig
from app.history_store import SessionHistoryStore
from app.engine.resolver import (
    HistoryEntryNotFoundError,
    SessionRequiredError,
    resolve_variables,
    resolve_runtime_variables,
    resolve_messages_variables,
    resolve_runtime_messages_variables,
    VariableNotFoundError,
    validate_variable_refs,
)


class TestResolveVariables:
    def test_single_variable(self):
        result = resolve_variables("{stt.qwen.result}", {"stt.qwen": "hello"})
        assert result == "hello"

    def test_multiple_variables(self):
        result = resolve_variables(
            "A: {stt.qwen.result}, B: {stt.whisper.result}",
            {"stt.qwen": "hello", "stt.whisper": "world"},
        )
        assert result == "A: hello, B: world"

    def test_no_variables(self):
        result = resolve_variables("plain text with no variables", {})
        assert result == "plain text with no variables"

    def test_missing_variable_raises(self):
        with pytest.raises(VariableNotFoundError):
            _ = resolve_variables("{nonexistent.block.result}", {})

    def test_json_braces_preserved(self):
        result = resolve_variables('Return JSON: {"key": "val"}', {})
        assert '{"key": "val"}' in result

    def test_empty_result_string(self):
        result = resolve_variables("{stt.qwen.result}", {"stt.qwen": ""})
        assert result == ""

    def test_partial_match_not_replaced(self):
        result = resolve_variables(
            "text {stt.qwen.result} more text", {"stt.qwen": "hello"}
        )
        assert result == "text hello more text"


class TestResolveRuntimeVariables:
    @pytest.mark.asyncio
    async def test_history_newest_and_oldest_indexes(self):
        store = SessionHistoryStore(max_history_length=5)
        await store.append("session-1", "stt.qwen", "oldest")
        await store.append("session-1", "stt.qwen", "middle")
        await store.append("session-1", "stt.qwen", "newest")

        newest = await resolve_runtime_variables(
            "{stt.qwen.history[0]}",
            {},
            session_history_store=store,
            user_session_id="session-1",
        )
        oldest = await resolve_runtime_variables(
            "{stt.qwen.history[-1]}",
            {},
            session_history_store=store,
            user_session_id="session-1",
        )

        assert newest == "newest"
        assert oldest == "oldest"

    @pytest.mark.asyncio
    async def test_result_and_history_coexist(self):
        store = SessionHistoryStore(max_history_length=5)
        await store.append("session-1", "stt.qwen", "prior")

        result = await resolve_runtime_variables(
            "now {stt.qwen.result} then {stt.qwen.history[0]}",
            {"stt.qwen": "current"},
            session_history_store=store,
            user_session_id="session-1",
        )

        assert result == "now current then prior"

    @pytest.mark.asyncio
    async def test_missing_session_raises(self):
        store = SessionHistoryStore(max_history_length=5)

        with pytest.raises(SessionRequiredError, match="Session required"):
            await resolve_runtime_variables(
                "{stt.qwen.history[-1]}",
                {},
                session_history_store=store,
            )

    @pytest.mark.asyncio
    async def test_missing_history_entry_raises(self):
        store = SessionHistoryStore(max_history_length=5)
        await store.append("session-1", "stt.qwen", "only")

        with pytest.raises(HistoryEntryNotFoundError, match="History entry not found"):
            await resolve_runtime_variables(
                "{stt.qwen.history[1]}",
                {},
                session_history_store=store,
                user_session_id="session-1",
            )


class TestValidateVariableRefs:
    def test_valid_pipeline_with_variable_refs(self):
        from app.config.schema import PipelineConfig

        cfg = PipelineConfig(
            output="{correct.final.result}",
            blocks=[
                BlockConfig(
                    tag="stt",
                    tasks=[
                        TaskConfig(
                            tag="qwen",
                            type="transcriptions",
                            model="local/qwen",
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
                                    role="user", content="Fix {stt.qwen.result}"
                                )
                            ],
                        )
                    ],
                ),
            ],
        )
        validate_variable_refs(cfg)

    def test_forward_reference_caught(self):
        block_b = BlockConfig(
            tag="b",
            tasks=[
                TaskConfig(
                    tag="y",
                    type="chat",
                    model="smart",
                    messages=[MessageConfig(role="user", content="Fix {a.x.result}")],
                )
            ],
        )
        block_a = BlockConfig(
            tag="a",
            tasks=[
                TaskConfig(
                    tag="x", type="transcriptions", model="local/qwen", need_audio=True
                )
            ],
        )
        cfg = SimpleNamespace(
            output="{b.y.result}",
            blocks=[block_b, block_a],
        )
        with pytest.raises(ValueError, match="[Rr]eferences|[Uu]ndefined|[Ee]arlier"):
            validate_variable_refs(cfg)

    def test_invalid_output_format(self):
        block_a = BlockConfig(
            tag="a",
            tasks=[
                TaskConfig(
                    tag="x",
                    type="chat",
                    model="smart",
                    messages=[MessageConfig(role="user", content="hello")],
                )
            ],
        )
        cfg = SimpleNamespace(
            output="invalid",
            blocks=[block_a],
        )
        with pytest.raises(ValueError, match="[Oo]utput"):
            validate_variable_refs(cfg)

    def test_output_references_nonexistent_task(self):
        block_a = BlockConfig(
            tag="a",
            tasks=[
                TaskConfig(
                    tag="x",
                    type="chat",
                    model="smart",
                    messages=[MessageConfig(role="user", content="hello")],
                )
            ],
        )
        cfg = SimpleNamespace(
            output="{nonexistent.task.result}",
            blocks=[block_a],
        )
        with pytest.raises(ValueError, match="no such"):
            validate_variable_refs(cfg)

    def test_history_reference_to_previous_block_is_valid(self):
        from app.config.schema import PipelineConfig

        cfg = PipelineConfig(
            output="{correct.final.result}",
            blocks=[
                BlockConfig(
                    tag="stt",
                    tasks=[
                        TaskConfig(
                            tag="qwen",
                            type="transcriptions",
                            model="local/qwen",
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
                                    content="Use {stt.qwen.history[-1]}",
                                )
                            ],
                        )
                    ],
                ),
            ],
        )

        validate_variable_refs(cfg)

    def test_history_forward_reference_caught(self):
        block_b = BlockConfig(
            tag="b",
            tasks=[
                TaskConfig(
                    tag="y",
                    type="chat",
                    model="smart",
                    messages=[
                        MessageConfig(role="user", content="Fix {a.x.history[-1]}")
                    ],
                )
            ],
        )
        block_a = BlockConfig(
            tag="a",
            tasks=[
                TaskConfig(
                    tag="x", type="transcriptions", model="local/qwen", need_audio=True
                )
            ],
        )
        cfg = SimpleNamespace(
            output="{b.y.result}",
            blocks=[block_b, block_a],
        )
        with pytest.raises(ValueError, match="[Rr]eferences|[Ee]arlier"):
            validate_variable_refs(cfg)

    def test_history_same_block_reference_caught(self):
        cfg = SimpleNamespace(
            output="{b.y.result}",
            blocks=[
                BlockConfig(
                    tag="b",
                    tasks=[
                        TaskConfig(
                            tag="x",
                            type="transcriptions",
                            model="local/qwen",
                            need_audio=True,
                        ),
                        TaskConfig(
                            tag="y",
                            type="chat",
                            model="smart",
                            messages=[
                                MessageConfig(
                                    role="user",
                                    content="Fix {b.x.history[0]}",
                                )
                            ],
                        ),
                    ],
                )
            ],
        )
        with pytest.raises(ValueError, match="[Rr]eferences|[Ee]arlier"):
            validate_variable_refs(cfg)

    def test_output_history_reference_is_rejected(self):
        block_a = BlockConfig(
            tag="a",
            tasks=[
                TaskConfig(
                    tag="x",
                    type="transcriptions",
                    model="local/qwen",
                    need_audio=True,
                )
            ],
        )
        cfg = SimpleNamespace(
            output="{a.x.history[-1]}",
            blocks=[block_a],
        )
        with pytest.raises(ValueError, match="[Oo]utput"):
            validate_variable_refs(cfg)


class TestResolveMessagesVariables:
    def test_single_variable_in_messages(self):
        messages = [{"role": "user", "content": "Fix {stt.qwen.result}"}]
        result = resolve_messages_variables(messages, {"stt.qwen": "hello"})
        assert result == [{"role": "user", "content": "Fix hello"}]

    def test_multiple_messages(self):
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Fix {stt.qwen.result}"},
        ]
        result = resolve_messages_variables(messages, {"stt.qwen": "hello"})
        assert result[0]["content"] == "You are helpful"
        assert result[1]["content"] == "Fix hello"

    def test_no_variables(self):
        messages = [{"role": "user", "content": "plain text"}]
        result = resolve_messages_variables(messages, {})
        assert result == [{"role": "user", "content": "plain text"}]

    def test_missing_variable_raises(self):
        messages = [{"role": "user", "content": "{nonexistent.block.result}"}]
        with pytest.raises(VariableNotFoundError):
            _ = resolve_messages_variables(messages, {})


class TestResolveRuntimeMessagesVariables:
    @pytest.mark.asyncio
    async def test_history_in_messages(self):
        store = SessionHistoryStore(max_history_length=5)
        await store.append("session-1", "stt.qwen", "latest")

        result = await resolve_runtime_messages_variables(
            [{"role": "user", "content": "Fix {stt.qwen.history[0]}"}],
            {},
            session_history_store=store,
            user_session_id="session-1",
        )

        assert result == [{"role": "user", "content": "Fix latest"}]
