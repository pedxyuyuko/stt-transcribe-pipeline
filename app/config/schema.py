from __future__ import annotations

import re

from pydantic import BaseModel, field_validator, model_validator
from typing import Any, Literal, Dict, List


HISTORY_REFERENCE_PATTERN = re.compile(
    r"\{[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.history\[[+-]?\d+\]\}"
)
VARIABLE_REFERENCE_PATTERN = re.compile(
    r"\{(?P<task_path>[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.(?:(?:result)|(?:history\[(?P<index>[+-]?\d+)\]))\}"
)


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str
    default_preset: str
    providers: Dict[str, "ProviderConfig"] = {}
    model_groups: Dict[str, List[str]] = {}
    log_level: str = "INFO"
    session_idle_timeout_minutes: int | None = None

    @field_validator("log_level")
    @classmethod
    def log_level_valid(cls, v: str) -> str:
        valid_levels = {"TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}, got '{v}'.")
        return v

    @field_validator("api_key")
    @classmethod
    def api_key_valid(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError(
                "api_key must contain only alphanumeric characters, underscores, and hyphens."
            )
        return v

    @field_validator("model_groups")
    @classmethod
    def model_group_entries_valid(cls, v: Dict[str, List[str]]) -> Dict[str, List[str]]:
        for group_name, entries in v.items():
            for entry in entries:
                if "/" not in entry:
                    raise ValueError(
                        f"Model group '{group_name}' entry '{entry}' is invalid. "
                        f"Must match 'provider_id/model_id' format (contains '/')."
                    )
        return v

    @field_validator("session_idle_timeout_minutes")
    @classmethod
    def session_idle_timeout_minutes_valid(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("session_idle_timeout_minutes must be a positive integer.")
        return v


class MessageConfig(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str
    require_session: bool = False
    missing_strategy: Literal["skip", "empty"] | None = None


class RecordConfig(BaseModel):
    enable: bool
    max_history_length: int | None = None

    @model_validator(mode="after")
    def record_fields_valid(self) -> RecordConfig:
        if self.max_history_length is not None and self.max_history_length <= 0:
            raise ValueError("'record.max_history_length' must be a positive integer.")
        if self.enable and self.max_history_length is None:
            raise ValueError(
                "'record.max_history_length' is required when 'record.enable' is true."
            )
        return self


class TaskConfig(BaseModel):
    tag: str
    type: Literal["chat", "transcriptions"]
    model: str
    need_audio: bool = False
    audio_format: Literal["input_audio", "audio_url"] = "input_audio"
    audio_force_transcode: Literal["wav", "mp3"] | None = None
    prompt: str | None = None
    messages: List[MessageConfig] | None = None
    record: RecordConfig | None = None
    max_retries: int = 0
    timeout: float | None = None
    model_params: Dict[str, Any] | None = None

    @model_validator(mode="after")
    def task_fields_valid(self) -> TaskConfig:
        if self.type == "chat":
            if not self.messages and not self.need_audio:
                raise ValueError(
                    "Chat task must have non-empty 'messages' unless 'need_audio' is true."
                )
            if self.prompt is not None:
                raise ValueError(
                    "Chat task must not use 'prompt'; use 'messages' instead."
                )
            if self.audio_force_transcode is not None and not self.need_audio:
                raise ValueError(
                    "Chat task with 'audio_force_transcode' must set 'need_audio' to true."
                )
            if self.messages:
                for message in self.messages:
                    has_history_reference = bool(
                        HISTORY_REFERENCE_PATTERN.search(message.content)
                    )
                    if message.missing_strategy is not None and not (
                        message.require_session or has_history_reference
                    ):
                        raise ValueError(
                            "Chat message 'missing_strategy' is only allowed when "
                            + "'require_session' is true or the message content contains '.history[...]'."
                        )
        elif self.type == "transcriptions":
            if self.messages is not None:
                raise ValueError(
                    "Transcriptions task must not use 'messages'; use 'prompt' instead."
                )
        return self


class BlockConfig(BaseModel):
    tag: str
    name: str | None = None
    tasks: List[TaskConfig]
    checkpoint: str | None = None

    @model_validator(mode="after")
    def task_tags_unique(self) -> BlockConfig:
        tags = [t.tag for t in self.tasks]
        if len(tags) != len(set(tags)):
            raise ValueError(f"Duplicate task tags in block '{self.tag}': {tags}")
        return self

    @model_validator(mode="after")
    def checkpoint_valid(self) -> BlockConfig:
        if self.checkpoint is not None:
            task_tags = {t.tag for t in self.tasks}
            if self.checkpoint not in task_tags:
                raise ValueError(
                    f"Block '{self.tag}' checkpoint '{self.checkpoint}' "
                    f"does not match any task tag in this block. Available: {task_tags}"
                )
        return self


class PipelineConfig(BaseModel):
    output: str
    blocks: List[BlockConfig]

    @model_validator(mode="after")
    def block_tags_unique(self) -> PipelineConfig:
        tags = [b.tag for b in self.blocks]
        if len(tags) != len(set(tags)):
            raise ValueError(f"Duplicate block tags: {tags}")
        return self

    @model_validator(mode="after")
    def validate_output_format(self) -> PipelineConfig:
        if not re.match(r"^\{[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.result\}$", self.output):
            raise ValueError(
                f"Invalid output format: '{self.output}'. Must be '{{block_tag.task_tag.result}}'"
            )
        return self

    @model_validator(mode="after")
    def validate_variable_refs(self) -> PipelineConfig:
        seen: set[str] = set()
        for block in self.blocks:
            block_refs: set[str] = set()
            for task in block.tasks:
                content_strings: list[str] = []
                if task.prompt:
                    content_strings.append(task.prompt)
                if task.messages:
                    content_strings.extend(msg.content for msg in task.messages)
                for text in content_strings:
                    refs = [
                        match.group("task_path")
                        for match in VARIABLE_REFERENCE_PATTERN.finditer(text)
                    ]
                    for ref in refs:
                        if ref not in seen:
                            raise ValueError(
                                f"Task '{block.tag}.{task.tag}' references undefined variable '{{{ref}.result}}'. "
                                f"Variables must reference blocks/tasks that appear earlier in the pipeline."
                            )
                block_refs.add(f"{block.tag}.{task.tag}")
            seen.update(block_refs)
        return self


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str
    headers: Dict[str, str] | None = None
