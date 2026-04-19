import pytest
from pydantic import ValidationError
from pathlib import Path
import tempfile

from app.config.schema import (
    AppConfig,
    BlockConfig,
    MessageConfig,
    PipelineConfig,
    ProviderConfig,
    TaskConfig,
)
from app.config.loader import load_all_configs, ConfigError


def make_chat_task(tag: str, content: str = "hello") -> TaskConfig:
    return TaskConfig(
        tag=tag,
        type="chat",
        model="smart",
        messages=[MessageConfig(role="user", content=content)],
    )


class TestAppConfig:
    def test_valid_app_config(self):
        cfg = AppConfig(
            host="127.0.0.1",
            port=9000,
            api_key="sk-test123",
            default_preset="default",
        )
        assert cfg.default_preset == "default"
        assert cfg.api_key == "sk-test123"

    def test_missing_default_preset(self):
        with pytest.raises(ValidationError):
            _ = AppConfig.model_validate(
                {"api_key": "sk-test", "host": "0.0.0.0", "port": 8000}
            )

    def test_invalid_api_key_format(self):
        with pytest.raises(ValidationError, match="alphanumeric"):
            AppConfig(
                api_key="invalid key!@#",
                host="0.0.0.0",
                port=8000,
                default_preset="default",
            )

    def test_valid_api_key_format(self):
        cfg = AppConfig(
            api_key="sk-abc_123-xyz",
            host="0.0.0.0",
            port=8000,
            default_preset="default",
        )
        assert cfg.api_key == "sk-abc_123-xyz"

    def test_valid_models_inline(self):
        cfg = AppConfig(
            api_key="sk-test",
            default_preset="default",
            providers={"openai": ProviderConfig(base_url="http://x", api_key="k")},
            model_groups={"smart": ["openai/gpt-4o"]},
        )
        assert "openai" in cfg.providers
        assert "smart" in cfg.model_groups

    def test_invalid_model_group_entry_no_slash(self):
        with pytest.raises(ValidationError, match="invalid"):
            AppConfig(
                api_key="sk-test",
                default_preset="default",
                providers={},
                model_groups={"bad": ["invalid-model"]},
            )

    def test_valid_provider_config(self):
        p = ProviderConfig(base_url="http://localhost:8000/v1", api_key="none")
        assert p.base_url == "http://localhost:8000/v1"


class TestPipelineConfig:
    def test_valid_pipeline_config(self):
        cfg = PipelineConfig(
            output="{stt.qwen.result}",
            blocks=[
                BlockConfig(
                    tag="stt",
                    tasks=[
                        TaskConfig(
                            tag="qwen",
                            type="transcriptions",
                            model="local/qwen3",
                            need_audio=True,
                        )
                    ],
                )
            ],
        )
        assert len(cfg.blocks) == 1

    def test_duplicate_block_tags(self):
        with pytest.raises(ValidationError, match="[Dd]uplicate"):
            PipelineConfig(
                output="{a.x.result}",
                blocks=[
                    BlockConfig(tag="a", tasks=[make_chat_task("x")]),
                    BlockConfig(tag="a", tasks=[make_chat_task("y")]),
                ],
            )

    def test_duplicate_task_tags_within_block(self):
        with pytest.raises(ValidationError, match="[Dd]uplicate"):
            PipelineConfig(
                output="{a.x.result}",
                blocks=[
                    BlockConfig(
                        tag="a", tasks=[make_chat_task("x"), make_chat_task("x")]
                    )
                ],
            )

    def test_invalid_output_format(self):
        with pytest.raises(ValidationError, match="[Oo]utput"):
            PipelineConfig(
                output="not-a-variable",
                blocks=[BlockConfig(tag="a", tasks=[make_chat_task("x")])],
            )

    def test_forward_variable_reference(self):
        with pytest.raises(ValidationError, match="[Uu]ndefined|[Rr]eferences"):
            PipelineConfig(
                output="{b.y.result}",
                blocks=[
                    BlockConfig(
                        tag="b", tasks=[make_chat_task("y", "Fix {a.x.result}")]
                    ),
                    BlockConfig(
                        tag="a",
                        tasks=[
                            TaskConfig(
                                tag="x",
                                type="transcriptions",
                                model="local/qwen",
                                need_audio=True,
                            )
                        ],
                    ),
                ],
            )

    def test_valid_variable_reference_same_block_not_allowed(self):
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
                    tasks=[make_chat_task("final", "Fix {stt.qwen.result}")],
                ),
            ],
        )
        assert cfg.output == "{correct.final.result}"

    def test_chat_task_allows_missing_messages_with_audio(self):
        task = TaskConfig(tag="x", type="chat", model="smart", need_audio=True)
        assert task.messages is None

    def test_chat_task_requires_messages_without_audio(self):
        with pytest.raises(ValidationError, match="[Mm]essages"):
            _ = TaskConfig(tag="x", type="chat", model="smart")

    def test_chat_task_forbids_prompt(self):
        with pytest.raises(ValidationError, match="[Pp]rompt"):
            _ = TaskConfig(
                tag="x",
                type="chat",
                model="smart",
                messages=[MessageConfig(role="user", content="hello")],
                prompt="should not be here",
            )

    def test_transcriptions_task_forbids_messages(self):
        with pytest.raises(ValidationError, match="[Mm]essages"):
            _ = TaskConfig(
                tag="x",
                type="transcriptions",
                model="local/qwen",
                messages=[MessageConfig(role="user", content="hello")],
            )

    def test_transcriptions_task_allows_prompt(self):
        t = TaskConfig(tag="x", type="transcriptions", model="local/qwen", prompt="ctx")
        assert t.prompt == "ctx"

    def test_chat_task_allows_empty_messages_with_audio(self):
        task = TaskConfig(
            tag="x", type="chat", model="smart", need_audio=True, messages=[]
        )
        assert task.messages == []

    def test_chat_task_rejects_empty_messages_without_audio(self):
        with pytest.raises(ValidationError, match="[Mm]essages"):
            _ = TaskConfig(tag="x", type="chat", model="smart", messages=[])


def _full_config(
    host: str = "0.0.0.0",
    port: int = 8000,
    api_key: str = "sk-test",
    default_preset: str = "test",
    providers: str = "  openai:\n    base_url: http://x\n    api_key: k\n  local-qwen:\n    base_url: http://localhost:8000/v1\n    api_key: none",
    model_groups: str = "  smart:\n    - openai/gpt-4o",
    extra: str = "",
) -> str:
    return (
        f"host: {host}\n"
        f"port: {port}\n"
        f"api_key: {api_key}\n"
        f"default_preset: {default_preset}\n"
        f"providers:\n{providers}\n"
        f"model_groups:\n{model_groups}\n"
        f"{extra}"
    )


class TestLoadAllConfigs:
    def test_load_valid_configs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "config.yml").write_text(_full_config(default_preset="default"))
            (tmppath / "presets").mkdir()
            # Create a minimal preset matching the default preset
            (tmppath / "presets" / "default.yaml").write_text(
                'output: "{a.x.result}"\nblocks:\n  - tag: a\n    tasks:\n      - tag: x\n        type: chat\n        model: smart\n        messages:\n          - role: user\n            content: hello'
            )
            app_cfg, presets = load_all_configs(tmppath)
            assert app_cfg.default_preset == "default"
            assert "default" in presets
            assert "openai" in app_cfg.providers
            assert "smart" in app_cfg.model_groups

    def test_nonexistent_directory(self):
        with pytest.raises(ConfigError):
            load_all_configs(Path("/nonexistent/path"))

    def test_missing_preset_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "config.yml").write_text(_full_config(default_preset="test"))
            with pytest.raises(
                ConfigError, match="[Pp]resets|[Dd]irectory|[Nn]ot found"
            ):
                load_all_configs(tmppath)

    def test_default_preset_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "config.yml").write_text(
                _full_config(default_preset="nonexistent")
            )
            (tmppath / "presets").mkdir()
            (tmppath / "presets" / "other.yaml").write_text(
                'output: "{a.x.result}"\nblocks:\n  - tag: a\n    tasks:\n      - tag: x\n        type: chat\n        model: smart\n        messages:\n          - role: user\n            content: hello'
            )
            with pytest.raises(ConfigError, match="default_preset|[Pp]reset"):
                load_all_configs(tmppath)

    def test_model_group_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "config.yml").write_text(_full_config())
            (tmppath / "presets").mkdir()
            (tmppath / "presets" / "test.yaml").write_text(
                'output: "{a.x.result}"\nblocks:\n  - tag: a\n    tasks:\n      - tag: x\n        type: chat\n        model: nonexistent_group\n        messages:\n          - role: user\n            content: hello'
            )
            with pytest.raises(ConfigError, match="model_group|not.*exist"):
                load_all_configs(tmppath)

    def test_provider_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "config.yml").write_text(
                _full_config(
                    providers="  other:\n    base_url: http://x\n    api_key: k"
                )
            )
            (tmppath / "presets").mkdir()
            (tmppath / "presets" / "test.yaml").write_text(
                'output: "{a.x.result}"\nblocks:\n  - tag: a\n    tasks:\n      - tag: x\n        type: transcriptions\n        model: openai/whisper\n        need_audio: true'
            )
            with pytest.raises(ConfigError, match="provider|not.*exist"):
                load_all_configs(tmppath)
