import pytest
from types import SimpleNamespace

from app.engine.resolver import (
    resolve_variables,
    resolve_messages_variables,
    VariableNotFoundError,
    validate_variable_refs,
)
from app.config.schema import BlockConfig, MessageConfig, TaskConfig


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
