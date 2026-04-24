"""Pipeline engine — block orchestration for the STT pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any

import httpx

from app.config.schema import PipelineConfig, AppConfig, TaskConfig
from app.engine.resolver import (
    HISTORY_REFERENCE_PATTERN,
    HistoryEntryNotFoundError,
    SessionRequiredError,
    resolve_variables,
    resolve_runtime_variables,
    ResultStore,
)
from app.history_store import SessionHistoryStore
from app.services.providers import (
    ProviderClient,
    resolve_model,
    call_with_fallback,
    AllModelsFailedError,
)
from app.services.stt import execute_stt_task

from app.services.llm import execute_chat_task
from app.services.audio import (
    build_transcoded_filename,
    get_audio_mime_type,
    transcode_audio,
)
from loguru import logger


class PipelineError(Exception):
    """Raised when a pipeline task fails and no checkpoint can be used."""

    block_tag: str
    task_tag: str
    original_error: Exception

    def __init__(self, block_tag: str, task_tag: str, original_error: Exception):
        self.block_tag = block_tag
        self.task_tag = task_tag
        self.original_error = original_error
        super().__init__(
            f"Pipeline error in block '{block_tag}', task '{task_tag}': {original_error}"
        )


class PipelineFallback(Exception):
    """Raised when a pipeline task fails, but a checkpointed value is available as fallback."""

    failed_block: str
    failed_task: str
    fallback_value: str
    results: ResultStore
    original_error: Exception

    def __init__(
        self,
        failed_block: str,
        failed_task: str,
        fallback_value: str,
        results: ResultStore,
        original_error: Exception,
    ):
        self.failed_block = failed_block
        self.failed_task = failed_task
        self.fallback_value = fallback_value
        self.results = results
        self.original_error = original_error
        super().__init__(
            f"Pipeline failed at '{failed_block}.{failed_task}', returning checkpoint fallback"
        )


class EmptyChatMessagesError(Exception):
    """Raised when chat preprocessing removes every message from a text-only task."""

    def __init__(self, task_path: str):
        self.task_path = task_path
        super().__init__(
            f"Chat task '{task_path}' has no messages left after session-aware filtering and requires text messages when need_audio is false."
        )


def _get_session_history_store(
    session_history_store: SessionHistoryStore | None,
) -> SessionHistoryStore | None:
    if session_history_store is not None:
        return session_history_store

    try:
        from main import app
    except Exception:
        return None

    return getattr(app.state, "session_history_store", None)


async def _resolve_message_content_with_empty_history(
    content: str,
    results: ResultStore,
    *,
    session_history_store: SessionHistoryStore | None,
    user_session_id: str | None,
) -> str:
    resolved_content = resolve_variables(content, results)
    history_matches = list(HISTORY_REFERENCE_PATTERN.finditer(resolved_content))
    if not history_matches:
        return resolved_content

    if user_session_id is None or session_history_store is None:
        resolved_parts: list[str] = []
        last_end = 0
        for match in history_matches:
            resolved_parts.append(resolved_content[last_end : match.start()])
            reference = match.group(0)[1:-1]
            logger.debug(
                "History reference missing without session, substituting empty string | reference={}",
                reference,
            )
            resolved_parts.append("")
            last_end = match.end()
        resolved_parts.append(resolved_content[last_end:])
        return "".join(resolved_parts)

    resolved_parts: list[str] = []
    last_end = 0
    for match in history_matches:
        resolved_parts.append(resolved_content[last_end : match.start()])
        task_path = match.group("task_path")
        index = int(match.group("index"))
        reference = match.group(0)[1:-1]
        history = await session_history_store.read(user_session_id, task_path)
        resolved_index = index if index >= 0 else len(history) + index
        if 0 <= resolved_index < len(history):
            resolved_parts.append(history[resolved_index])
        else:
            logger.debug(
                "History reference missing, substituting empty string | reference={} | user_session_id={}",
                reference,
                user_session_id,
            )
            resolved_parts.append("")
        last_end = match.end()
    resolved_parts.append(resolved_content[last_end:])
    return "".join(resolved_parts)


async def _prepare_chat_messages(
    task: TaskConfig,
    results: ResultStore,
    *,
    task_path: str,
    session_history_store: SessionHistoryStore | None,
    user_session_id: str | None,
) -> list[dict[str, str]]:
    prepared_messages: list[dict[str, str]] = []

    for message in task.messages or []:
        if message.require_session and user_session_id is None:
            logger.debug(
                "Skipping chat message without required session | task_path={} | role={}",
                task_path,
                message.role,
            )
            continue

        try:
            resolved_content = await resolve_runtime_variables(
                message.content,
                results,
                session_history_store=session_history_store,
                user_session_id=user_session_id,
            )
        except (HistoryEntryNotFoundError, SessionRequiredError) as exc:
            if message.missing_strategy == "skip":
                logger.debug(
                    "Skipping chat message due to missing history | task_path={} | reference={}",
                    task_path,
                    exc.reference,
                )
                continue
            if message.missing_strategy == "empty":
                resolved_content = await _resolve_message_content_with_empty_history(
                    message.content,
                    results,
                    session_history_store=session_history_store,
                    user_session_id=user_session_id,
                )
            else:
                raise

        prepared_messages.append(
            {"role": message.role, "content": resolved_content}
        )

    if not prepared_messages and not task.need_audio:
        raise EmptyChatMessagesError(task_path)

    return prepared_messages


async def _record_task_result(
    task: TaskConfig,
    *,
    task_path: str,
    result: str,
    session_history_store: SessionHistoryStore | None,
    user_session_id: str | None,
) -> None:
    if user_session_id is None:
        return
    if task.record is None or not task.record.enable:
        return
    if session_history_store is None:
        return

    await session_history_store.append(user_session_id, task_path, result)
    if task.record.max_history_length is not None:
        await session_history_store.truncate(
            user_session_id,
            task_path,
            max_history_length=task.record.max_history_length,
        )

    logger.debug(
        "Recorded task result to session history | task_path={} | user_session_id={}",
        task_path,
        user_session_id,
    )


async def _call_task_with_retries(
    max_retries: int,
    *,
    models: list[tuple[ProviderClient, str]],
    call_fn,
    task_path: str,
) -> str:
    """Retry call_with_fallback on AllModelsFailedError / ConnectError."""
    attempt = 0
    while True:
        try:
            return await call_with_fallback(
                models=models,
                call_fn=call_fn,
                task_path=task_path,
            )
        except (
            AllModelsFailedError,
            httpx.ConnectError,
        ) as exc:
            attempt += 1
            if attempt > max_retries:
                raise

            def _exception_detail(exc: Exception) -> str:
                msg = str(exc)
                return msg if msg else f"[{type(exc).__name__}]"

            logger.warning(
                "Task '{}' failed (attempt {}/{}), retrying | {}",
                task_path,
                attempt,
                max_retries + 1,
                _exception_detail(exc),
                exc_info=True,
            )


async def run_pipeline(
    preset: PipelineConfig,
    models_config: AppConfig,
    client: httpx.AsyncClient,
    audio_bytes: bytes,
    audio_filename: str = "audio.wav",
    audio_input_format: str = "wav",
    user_session_id: str | None = None,
    session_history_store: SessionHistoryStore | None = None,
) -> ResultStore:
    results: ResultStore = {}
    last_checkpoint_value: str | None = None
    history_store = _get_session_history_store(session_history_store)

    logger.debug("Starting pipeline | blocks={}", len(preset.blocks))

    for block in preset.blocks:
        logger.debug(
            "Executing block '{}' | tasks={}",
            block.tag,
            [t.tag for t in block.tasks],
        )
        coros: list[Awaitable[str]] = []
        task_keys: list[tuple[str, str, TaskConfig]] = []

        for task in block.tasks:
            model_list: list[tuple[ProviderClient, str]] = resolve_model(
                task.model, models_config
            )
            task_path = f"{block.tag}.{task.tag}"
            task_keys.append((block.tag, task.tag, task))
            logger.debug(
                "  Task '{}.{}' | type={} | model={}",
                block.tag,
                task.tag,
                task.type,
                task.model,
            )

            if task.type == "transcriptions":
                stt_audio_bytes = audio_bytes
                stt_audio_filename = audio_filename
                stt_content_type = get_audio_mime_type(audio_input_format)
                if task.audio_force_transcode is not None:
                    stt_audio_bytes = await transcode_audio(
                        audio_bytes=audio_bytes,
                        source_format=audio_input_format,
                        target_format=task.audio_force_transcode,
                    )
                    stt_audio_filename = build_transcoded_filename(
                        task.audio_force_transcode
                    )
                    stt_content_type = get_audio_mime_type(task.audio_force_transcode)

                async def _stt(
                    pc: ProviderClient,
                    mn: str,
                    t: TaskConfig = task,
                    stt_bytes: bytes = stt_audio_bytes,
                    stt_filename: str = stt_audio_filename,
                    stt_ct: str = stt_content_type,
                ):
                    return await execute_stt_task(
                        provider_client=pc,
                        task=t,
                        audio_bytes=stt_bytes,
                        client=client,
                        model_name=mn,
                        filename=stt_filename,
                        content_type=stt_ct,
                    )

                coros.append(
                    _call_task_with_retries(
                        max_retries=getattr(task, "max_retries", 0),
                        models=model_list,
                        call_fn=_stt,
                        task_path=task_path,
                    )
                )

            elif task.type == "chat":
                async def _chat(
                    pc: ProviderClient,
                    mn: str,
                    t: TaskConfig = task,
                    tp: str = task_path,
                ):
                    resolved_messages = await _prepare_chat_messages(
                        t,
                        results,
                        task_path=tp,
                        session_history_store=history_store,
                        user_session_id=user_session_id,
                    )
                    audio = audio_bytes if t.need_audio else None
                    chat_audio_input_format = audio_input_format
                    if t.need_audio and t.audio_force_transcode is not None:
                        audio = await transcode_audio(
                            audio_bytes=audio_bytes,
                            source_format=audio_input_format,
                            target_format=t.audio_force_transcode,
                        )
                        chat_audio_input_format = t.audio_force_transcode

                    return await execute_chat_task(
                        provider_client=pc,
                        task=t,
                        resolved_messages=[dict(message) for message in resolved_messages],
                        audio_bytes=audio,
                        audio_input_format=chat_audio_input_format,
                        client=client,
                        model_name=mn,
                    )

                coros.append(
                    _call_task_with_retries(
                        max_retries=getattr(task, "max_retries", 0),
                        models=model_list,
                        call_fn=_chat,
                        task_path=task_path,
                    )
                )
        task_results: list[str | BaseException] = await asyncio.gather(
            *coros, return_exceptions=True
        )

        try:
            for i, result in enumerate(task_results):
                block_tag, task_tag, task = task_keys[i]
                if isinstance(result, BaseException):
                    if not isinstance(result, Exception):
                        raise result
                    raise PipelineError(
                        block_tag=block_tag,
                        task_tag=task_tag,
                        original_error=result,
                    )
                task_path = f"{block_tag}.{task_tag}"
                results[task_path] = result
                await _record_task_result(
                    task,
                    task_path=task_path,
                    result=result,
                    session_history_store=history_store,
                    user_session_id=user_session_id,
                )
                logger.debug(
                    "  Task '{}.{}' completed | result_length={}",
                    block_tag,
                    task_tag,
                    len(result) if isinstance(result, str) else "N/A",
                )
        except PipelineError as e:
            if last_checkpoint_value is not None:
                logger.warning(
                    "Block '{}' failed, returning checkpoint fallback | {}",
                    block.tag,
                    e.original_error,
                    exc_info=True,
                )
                raise PipelineFallback(
                    failed_block=e.block_tag,
                    failed_task=e.task_tag,
                    fallback_value=last_checkpoint_value,
                    results=results,
                    original_error=e.original_error,
                ) from e.original_error
            raise

        if block.checkpoint is not None:
            checkpoint_key = f"{block.tag}.{block.checkpoint}"
            if checkpoint_key in results:
                last_checkpoint_value = results[checkpoint_key]
                logger.debug(
                    "Block '{}' checkpoint stored | value_length={}",
                    block.tag,
                    len(last_checkpoint_value),
                )

    logger.debug("Pipeline finished | total_results={}", len(results))
    return results


def get_pipeline_output(output_template: str, results: ResultStore) -> str:
    return resolve_variables(output_template, results)
