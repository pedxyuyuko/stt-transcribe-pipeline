"""STT task executor — transcriptions type."""

from __future__ import annotations

import httpx

from loguru import logger

from app.config.schema import TaskConfig
from app.services.providers import ProviderClient


async def execute_stt_task(
    provider_client: ProviderClient,
    task: TaskConfig,
    audio_bytes: bytes,
    client: httpx.AsyncClient,
    model_name: str,
) -> str:
    """
    Execute an STT transcription task.

    Sends audio as multipart form to the provider's /audio/transcriptions endpoint.
    Returns the "text" field from the JSON response.

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
        "STT task executing | model={} | audio_size={} bytes | prompt={}",
        model_name,
        len(audio_bytes),
        task.prompt or "(none)",
    )

    result = await provider_client.post_transcription(
        client=client,
        audio_bytes=audio_bytes,
        model=model_name,
        prompt=task.prompt,
        timeout=task.timeout,
    )
    logger.debug("STT task output: {}", result)
    return result
