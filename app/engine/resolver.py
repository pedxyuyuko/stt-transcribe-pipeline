"""
Variable resolver for the STT pipeline.

Handles {block_tag.task_tag.result} string substitution at runtime,
and static validation of variable references at config load time.
"""

from __future__ import annotations

import re
from typing import Dict

from app.history_store import SessionHistoryStore


class VariableNotFoundError(Exception):
    """Raised when a variable reference cannot be resolved."""

    def __init__(self, reference: str):
        self.reference = reference
        super().__init__(f"Variable not found: {{{reference}.result}}")


class SessionHistoryResolutionError(Exception):
    """Base class for session-history resolution errors."""


class SessionRequiredError(SessionHistoryResolutionError):
    """Raised when a history reference is used without a session id."""

    def __init__(self, reference: str):
        self.reference = reference
        super().__init__(
            f"Session required to resolve history reference: {{{reference}}}"
        )


class HistoryEntryNotFoundError(SessionHistoryResolutionError):
    """Raised when a history reference points to a missing retained entry."""

    def __init__(self, reference: str, task_path: str, index: int):
        self.reference = reference
        self.task_path = task_path
        self.index = index
        super().__init__(
            "History entry not found for reference: "
            + f"{{{reference}}} (task='{task_path}', index={index})"
        )


# Use ResultStore type alias (just a regular Dict[str, str])
ResultStore = Dict[str, str]


RESULT_REFERENCE_PATTERN = re.compile(
    r"\{(?P<task_path>[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}"
)
HISTORY_REFERENCE_PATTERN = re.compile(
    r"\{(?P<task_path>[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.history\[(?P<index>[+-]?\d+)\]\}"
)
ANY_REFERENCE_PATTERN = re.compile(
    r"\{(?P<task_path>[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.(?:(?:result)|(?:history\[(?P<index>[+-]?\d+)\]))\}"
)


def _resolve_history_index(history: list[str], index: int, reference: str, task_path: str) -> str:
    resolved_index = index if index >= 0 else len(history) + index
    if resolved_index < 0 or resolved_index >= len(history):
        raise HistoryEntryNotFoundError(reference, task_path, index)
    return history[resolved_index]


def _validate_output_reference(output: str):
    output_match = re.match(
        r"^\{([a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)\.result\}$", output
    )
    if not output_match:
        raise ValueError(
            f"Invalid output format: '{output}'. Must be '{{block_tag.task_tag.result}}'"
        )
    return output_match


def _iter_references(text: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for match in ANY_REFERENCE_PATTERN.finditer(text):
        access_mode = "result" if match.group("index") is None else "history"
        refs.append((match.group("task_path"), access_mode))
    return refs


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
    def replacer(match: re.Match[str]) -> str:
        key = match.group("task_path")
        if key not in results:
            raise VariableNotFoundError(match.group(0))
        return results[key]

    return RESULT_REFERENCE_PATTERN.sub(replacer, template)


async def resolve_runtime_variables(
    template: str,
    results: ResultStore,
    *,
    session_history_store: SessionHistoryStore | None = None,
    user_session_id: str | None = None,
) -> str:
    """Resolve .result and .history[index] references for runtime message assembly."""

    result = resolve_variables(template, results)
    history_matches = list(HISTORY_REFERENCE_PATTERN.finditer(result))
    if not history_matches:
        return result

    if user_session_id is None:
        first_reference = history_matches[0].group(0)[1:-1]
        raise SessionRequiredError(first_reference)
    if session_history_store is None:
        first_reference = history_matches[0].group(0)[1:-1]
        raise SessionRequiredError(first_reference)

    resolved_parts: list[str] = []
    last_end = 0
    for match in history_matches:
        resolved_parts.append(result[last_end : match.start()])
        task_path = match.group("task_path")
        index = int(match.group("index"))
        reference = match.group(0)[1:-1]
        history = await session_history_store.read(user_session_id, task_path)
        resolved_parts.append(
            _resolve_history_index(history, index, reference, task_path)
        )
        last_end = match.end()
    resolved_parts.append(result[last_end:])
    return "".join(resolved_parts)


def resolve_messages_variables(
    messages: list[dict[str, str]], results: ResultStore
) -> list[dict[str, str]]:
    """Resolve {block.task.result} patterns in each message's content string."""
    return [
        {**msg, "content": resolve_variables(msg["content"], results)}
        for msg in messages
    ]


async def resolve_runtime_messages_variables(
    messages: list[dict[str, str]],
    results: ResultStore,
    *,
    session_history_store: SessionHistoryStore | None = None,
    user_session_id: str | None = None,
) -> list[dict[str, str]]:
    """Resolve .result and .history[index] patterns in each message content string."""
    resolved_messages: list[dict[str, str]] = []
    for msg in messages:
        resolved_messages.append(
            {
                **msg,
                "content": await resolve_runtime_variables(
                    msg["content"],
                    results,
                    session_history_store=session_history_store,
                    user_session_id=user_session_id,
                ),
            }
        )
    return resolved_messages


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
    output_match = _validate_output_reference(pipeline.output)

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
        block_refs: set[str] = set()
        for task in block.tasks:
            content_strings: list[str] = []
            if task.prompt:
                content_strings.append(task.prompt)
            if task.messages:
                content_strings.extend(msg.content for msg in task.messages)
            for text in content_strings:
                refs = _iter_references(text)
                for ref, access_mode in refs:
                    if ref not in seen:
                        raise ValueError(
                            f"Task '{block.tag}.{task.tag}' references variable '{{{ref}.{access_mode}}}' "
                            f"but it doesn't appear earlier in the pipeline. "
                            f"Available (from previous blocks): {seen}"
                        )
            block_refs.add(f"{block.tag}.{task.tag}")
        # Mark this block's tasks as available only for LATER blocks
        seen.update(block_refs)
