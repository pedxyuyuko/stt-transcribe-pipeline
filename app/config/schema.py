from __future__ import annotations

import re

from pydantic import BaseModel, field_validator, model_validator
from typing import Any, Literal, Dict, List


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str
    default_preset: str
    providers: Dict[str, ProviderConfig] = {}
    model_groups: Dict[str, List[str]] = {}
    log_level: str = "INFO"

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


class TaskConfig(BaseModel):
    tag: str
    type: Literal["chat", "transcriptions"]
    model: str
    need_audio: bool = False
    prompt: str | None = None
    max_retries: int = 0
    timeout: float | None = None
    model_params: Dict[str, Any] | None = None


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
            for task in block.tasks:
                if task.prompt:
                    refs = re.findall(
                        r"\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}", task.prompt
                    )
                    for ref in refs:
                        if ref not in seen:
                            raise ValueError(
                                f"Task '{block.tag}.{task.tag}' references undefined variable '{{{ref}.result}}'. "
                                f"Variables must reference blocks/tasks that appear earlier in the pipeline."
                            )
                seen.add(f"{block.tag}.{task.tag}")
        return self


class ProviderConfig(BaseModel):
    base_url: str
    api_key: str
    headers: Dict[str, str] | None = None
