"""Pipeline engine — block orchestration for the STT pipeline."""

from __future__ import annotations

import asyncio

import httpx

from app.config.schema import PipelineConfig, AppConfig, TaskConfig
from app.engine.resolver import resolve_variables, ResultStore
from app.services.providers import (
    ProviderClient,
    resolve_model,
    call_with_fallback,
    AllModelsFailedError,
)
from app.services.stt import execute_stt_task

from app.services.llm import execute_chat_task
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
            logger.warning(
                "Task '{}' failed (attempt {}/{}), retrying | {}",
                task_path,
                attempt,
                max_retries + 1,
                exc,
                exc_info=True,
            )


async def run_pipeline(
    preset: PipelineConfig,
    models_config: AppConfig,
    client: httpx.AsyncClient,
    audio_bytes: bytes,
) -> ResultStore:
    results: ResultStore = {}
    last_checkpoint_value: str | None = None

    logger.debug("Starting pipeline | blocks={}", len(preset.blocks))

    for block in preset.blocks:
        logger.debug(
            "Executing block '{}' | tasks={}",
            block.tag,
            [t.tag for t in block.tasks],
        )
        coros: list[object] = []
        task_keys: list[tuple[str, str]] = []

        for task in block.tasks:
            model_list: list[tuple[ProviderClient, str]] = resolve_model(
                task.model, models_config
            )
            task_keys.append((block.tag, task.tag))
            logger.debug(
                "  Task '{}.{}' | type={} | model={}",
                block.tag,
                task.tag,
                task.type,
                task.model,
            )

            if task.type == "transcriptions":

                async def _stt(pc: ProviderClient, mn: str, t: TaskConfig = task):
                    return await execute_stt_task(
                        provider_client=pc,
                        task=t,
                        audio_bytes=audio_bytes,
                        client=client,
                        model_name=mn,
                    )

                coros.append(
                    _call_task_with_retries(
                        max_retries=getattr(task, "max_retries", 0),
                        models=model_list,
                        call_fn=_stt,
                        task_path=f"{block.tag}.{task.tag}",
                    )
                )

            elif task.type == "chat":
                resolved_prompt = task.prompt or ""
                if resolved_prompt:
                    resolved_prompt = resolve_variables(resolved_prompt, results)
                audio = audio_bytes if task.need_audio else None

                async def _chat(
                    pc: ProviderClient,
                    mn: str,
                    t: TaskConfig = task,
                    rp: str = resolved_prompt,
                    aud: bytes | None = audio,
                ):
                    return await execute_chat_task(
                        provider_client=pc,
                        task=t,
                        resolved_prompt=rp,
                        audio_bytes=aud,
                        client=client,
                        model_name=mn,
                    )

                coros.append(
                    _call_task_with_retries(
                        max_retries=getattr(task, "max_retries", 0),
                        models=model_list,
                        call_fn=_chat,
                        task_path=f"{block.tag}.{task.tag}",
                    )
                )
        task_results = await asyncio.gather(*coros, return_exceptions=True)

        try:
            for i, result in enumerate(task_results):
                block_tag, task_tag = task_keys[i]
                if isinstance(result, Exception):
                    raise PipelineError(
                        block_tag=block_tag,
                        task_tag=task_tag,
                        original_error=result,
                    )
                results[f"{block_tag}.{task_tag}"] = result
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
