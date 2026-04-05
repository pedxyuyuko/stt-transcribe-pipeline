"""
Variable resolver for the STT pipeline.

Handles {block_tag.task_tag.result} string substitution at runtime,
and static validation of variable references at config load time.
"""

from __future__ import annotations

import re
from typing import Dict


class VariableNotFoundError(Exception):
    """Raised when a variable reference cannot be resolved."""

    def __init__(self, reference: str):
        self.reference = reference
        super().__init__(f"Variable not found: {{{reference}.result}}")


# Use ResultStore type alias (just a regular Dict[str, str])
ResultStore = Dict[str, str]


def resolve_variables(template: str, results: ResultStore) -> str:
    """
    Find all {block_tag.task_tag.result} patterns in template and substitute from results.

    Only matches the pattern {word.word.result} — three dot-separated parts ending in 'result'.
    JSON-like braces like {"key": "val"} are NOT matched.

    Args:
        template: String potentially containing {block.task.result} references
        results: Dict mapping "block_tag.task_tag" → result text

    Returns:
        String with all variables substituted

    Raises:
        VariableNotFoundError: If a referenced variable is not in results
    """
    # Pattern: {block_tag.task_tag.result} — exactly word.word.result
    pattern = re.compile(r"\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}")

    def replacer(match: re.Match) -> str:
        key = match.group(1)  # "block_tag.task_tag"
        if key not in results:
            raise VariableNotFoundError(match.group(0))
        return results[key]

    return pattern.sub(replacer, template)


def validate_variable_refs(pipeline) -> None:
    """
    Static validation: scan all prompts for {block.task.result} patterns,
    verify each references a block_tag.task_tag that appears BEFORE this task.
    Also validates the `output` field.

    Args:
        pipeline: PipelineConfig object (from app.config.schema)

    Raises:
        ValueError: If any variable reference is invalid or forward-referencing
    """
    # Validate output field
    output_match = re.match(
        r"^\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}$", pipeline.output
    )
    if not output_match:
        raise ValueError(
            f"Invalid output format: '{pipeline.output}'. Must be '{{block_tag.task_tag.result}}'"
        )

    # Collect ALL block.task pairs that exist in the pipeline
    all_refs = set()
    for block in pipeline.blocks:
        for task in block.tasks:
            all_refs.add(f"{block.tag}.{task.tag}")

    # Validate output references an existing task
    output_ref = output_match.group(1)
    if output_ref not in all_refs:
        raise ValueError(
            f"Output references '{{{output_ref}.result}}' but no such block.task exists in the pipeline. "
            f"Available: {all_refs}"
        )

    # Validate EACH task's variable references
    # For a task at position (block_i, task_i), it can ONLY reference tasks from EARLIER blocks
    # OR earlier tasks in the same block — but since tasks in a block run in parallel,
    # it can only reference tasks from PREVIOUS blocks (not same block)
    seen: set[str] = set()
    for block in pipeline.blocks:
        for task in block.tasks:
            if task.prompt:
                refs = re.findall(
                    r"\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}", task.prompt
                )
                for ref in refs:
                    if ref not in seen:
                        raise ValueError(
                            f"Task '{block.tag}.{task.tag}' references variable '{{{ref}.result}}' "
                            f"but it doesn't appear earlier in the pipeline. "
                            f"Available (from previous blocks): {seen}"
                        )
            # Mark this task's result as available for LATER blocks
            seen.add(f"{block.tag}.{task.tag}")
