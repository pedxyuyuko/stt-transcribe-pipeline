"""Pipeline engine — block orchestration for the STT pipeline."""

from __future__ import annotations

import asyncio

import httpx

from app.config.schema import PipelineConfig, AppConfig, TaskConfig
from app.engine.resolver import resolve_variables, ResultStore
from app.services.providers import ProviderClient, resolve_model, call_with_fallback
from app.services.stt import execute_stt_task
from app.services.llm import execute_chat_task


class PipelineError(Exception):
    """Raised when a pipeline task fails."""

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


async def run_pipeline(
    preset: PipelineConfig,
    models_config: AppConfig,
    client: httpx.AsyncClient,
    audio_bytes: bytes,
) -> ResultStore:
    results: ResultStore = {}

    for block in preset.blocks:
        coros: list[object] = []
        task_keys: list[tuple[str, str]] = []

        for task in block.tasks:
            model_list: list[tuple[ProviderClient, str]] = resolve_model(
                task.model, models_config
            )
            task_keys.append((block.tag, task.tag))

            if task.type == "transcriptions":

                async def _stt(pc: ProviderClient, mn: str, t: TaskConfig = task):
                    return await execute_stt_task(
                        provider_client=pc,
                        task=t,
                        audio_bytes=audio_bytes,
                        client=client,
                        model_name=mn,
                    )

                coros.append(call_with_fallback(models=model_list, call_fn=_stt))

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

                coros.append(call_with_fallback(models=model_list, call_fn=_chat))

        task_results = await asyncio.gather(*coros, return_exceptions=True)

        for i, result in enumerate(task_results):
            block_tag, task_tag = task_keys[i]
            if isinstance(result, Exception):
                raise PipelineError(
                    block_tag=block_tag,
                    task_tag=task_tag,
                    original_error=result,
                )
            results[f"{block_tag}.{task_tag}"] = result

    return results


def get_pipeline_output(output_template: str, results: ResultStore) -> str:
    return resolve_variables(output_template, results)
