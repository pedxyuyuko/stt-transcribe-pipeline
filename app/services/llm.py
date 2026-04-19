"""LLM task executor — chat type with optional audio."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config.schema import TaskConfig
from app.services.providers import ProviderClient
from loguru import logger


async def execute_chat_task(
    provider_client: ProviderClient,
    task: TaskConfig,
    resolved_messages: list[dict[str, Any]],
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
        resolved_messages: Already variable-resolved messages list
        audio_bytes: Raw audio bytes or None
        client: Shared httpx.AsyncClient
        model_name: The model name (already resolved)

    Returns:
        The response content string

    Raises:
        httpx.HTTPStatusError: On non-2xx response (for fallback to catch)
    """
    logger.debug(
        "Chat task executing | model={} | has_audio={} | messages_count={}",
        model_name,
        audio_bytes is not None,
        len(resolved_messages),
    )
    messages: list[dict[str, Any]] = [dict(message) for message in resolved_messages]

    if audio_bytes is not None:
        b64 = base64.b64encode(audio_bytes).decode("utf-8")
        audio_content: dict[str, Any]
        if task.audio_format == "audio_url":
            audio_content = {
                "type": "audio_url",
                "audio_url": {"url": f"data:audio/wav;base64,{b64}"},
            }
        else:
            audio_content = {
                "type": "input_audio",
                "input_audio": {"data": b64, "format": "wav"},
            }
        # Append audio to the last user message's content.
        appended_to_user_message = False
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_content = messages[i].get("content", "")
                if isinstance(user_content, str):
                    messages[i] = {
                        **messages[i],
                        "content": [
                            {"type": "text", "text": user_content},
                            audio_content,
                        ],
                    }
                else:
                    messages[i]["content"].append(audio_content)
                appended_to_user_message = True
                break

        if not appended_to_user_message:
            messages.append({"role": "user", "content": [audio_content]})

    result = await provider_client.post_chat(
        client=client,
        messages=messages,
        model=model_name,
        timeout=task.timeout,
        model_params=task.model_params,
    )
    logger.debug("Chat task output: {}", result)
    return result
