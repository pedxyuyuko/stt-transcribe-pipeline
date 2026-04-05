import pytest
from types import SimpleNamespace

from app.engine.resolver import (
    resolve_variables,
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
            resolve_variables("{nonexistent.block.result}", {})

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
                {
                    "tag": "stt",
                    "tasks": [
                        {
                            "tag": "qwen",
                            "type": "transcriptions",
                            "model": "local/qwen",
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
                            "prompt": "Fix {stt.qwen.result}",
                        }
                    ],
                },
            ],
        )
        validate_variable_refs(cfg)

    def test_forward_reference_caught(self):
        from app.config.schema import BlockConfig, TaskConfig

        block_b = BlockConfig(
            tag="b",
            tasks=[
                TaskConfig(
                    tag="y", type="chat", model="smart", prompt="Fix {a.x.result}"
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
        from app.config.schema import BlockConfig, TaskConfig

        block_a = BlockConfig(
            tag="a",
            tasks=[TaskConfig(tag="x", type="chat", model="smart")],
        )
        cfg = SimpleNamespace(
            output="invalid",
            blocks=[block_a],
        )
        with pytest.raises(ValueError, match="[Oo]utput"):
            validate_variable_refs(cfg)

    def test_output_references_nonexistent_task(self):
        from app.config.schema import BlockConfig, TaskConfig

        block_a = BlockConfig(
            tag="a",
            tasks=[TaskConfig(tag="x", type="chat", model="smart")],
        )
        cfg = SimpleNamespace(
            output="{nonexistent.task.result}",
            blocks=[block_a],
        )
        with pytest.raises(ValueError, match="no such"):
            validate_variable_refs(cfg)
