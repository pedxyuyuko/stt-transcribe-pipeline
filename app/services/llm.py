"""LLM task executor — chat type with optional audio."""

from __future__ import annotations

import base64

import httpx

from app.config.schema import TaskConfig
from app.services.providers import ProviderClient
from loguru import logger


async def execute_chat_task(
    provider_client: ProviderClient,
    task: TaskConfig,
    resolved_prompt: str,
    audio_bytes: bytes | None,
    client: httpx.AsyncClient,
    model_name: str,
) -> str:
    """
    Execute an LLM chat task.

    Builds multimodal message content (text + optional audio) and POSTs to chat/completions.
    Returns content from the response.

    Args:
        provider_client: The resolved provider client
        task: TaskConfig with type "chat"
        resolved_prompt: Already variable-resolved prompt string
        audio_bytes: Raw audio bytes or None
        client: Shared httpx.AsyncClient
        model_name: The model name (already resolved)

    Returns:
        The response content string

    Raises:
        httpx.HTTPStatusError: On non-2xx response (for fallback to catch)
    """
    logger.debug(
        "Chat task executing | model={} | has_audio={} | prompt_length={}",
        model_name,
        audio_bytes is not None,
        len(resolved_prompt),
    )
    logger.debug("Chat task prompt: {}", resolved_prompt)

    content: list[dict] = [{"type": "text", "text": resolved_prompt}]

    if audio_bytes is not None:
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        content.append(
            {
                "type": "input_audio",
                "input_audio": {"data": b64, "format": "wav"},
            }
        )

    result = await provider_client.post_chat(
        client=client,
        messages=[{"role": "user", "content": content}],
        model=model_name,
        timeout=task.timeout,
    )
    logger.debug("Chat task output: {}", result)
    return result
