from __future__ import annotations

import re

from pydantic import BaseModel, field_validator, model_validator
from typing import Literal, Dict, List


class TaskConfig(BaseModel):
    tag: str
    type: Literal["chat", "transcriptions"]
    model: str
    need_audio: bool = False
    prompt: str | None = None


class BlockConfig(BaseModel):
    tag: str
    name: str | None = None
    tasks: List[TaskConfig]

    @model_validator(mode="after")
    def task_tags_unique(self) -> BlockConfig:
        tags = [t.tag for t in self.tasks]
        if len(tags) != len(set(tags)):
            raise ValueError(f"Duplicate task tags in block '{self.tag}': {tags}")
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


class ModelsConfig(BaseModel):
    providers: Dict[str, ProviderConfig]
    model_groups: Dict[str, List[str]]

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


class AppConfig(BaseModel):
    default_preset: str
