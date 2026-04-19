"""STT task executor — transcriptions type."""

from __future__ import annotations

import httpx

from loguru import logger

from app.config.schema import TaskConfig
from app.services.providers import ProviderClient


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"


async def execute_stt_task(
    provider_client: ProviderClient,
    task: TaskConfig,
    audio_bytes: bytes,
    client: httpx.AsyncClient,
    model_name: str,
    filename: str = "audio.wav",
    content_type: str = "application/octet-stream",
) -> str:
    """
    Execute an STT transcription task.

    POSTs audio to the provider's STT endpoint and returns the transcription text.

    Args:
        provider_client: The resolved provider client
        task: TaskConfig with type "transcriptions"
        audio_bytes: Raw audio bytes
        client: Shared httpx.AsyncClient
        model_name: The model name (already resolved from model_group or direct)

    Returns:
        The transcription text string

    Raises:
        httpx.HTTPStatusError: On non-2xx response (for fallback to catch)
    """
    logger.debug(
        "STT task executing | model={} | audio_size={} | prompt={}",
        model_name,
        _format_size(len(audio_bytes)),
        task.prompt or "(none)",
    )

    result = await provider_client.post_transcription(
        client=client,
        audio_bytes=audio_bytes,
        model=model_name,
        prompt=task.prompt,
        timeout=task.timeout,
        model_params=task.model_params,
        filename=filename,
        content_type=content_type,
    )
    logger.debug("STT task output: {}", result)
    return result
