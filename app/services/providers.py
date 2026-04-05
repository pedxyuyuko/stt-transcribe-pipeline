"""
HTTP client manager and provider abstraction for the STT pipeline.

Handles:
- Provider client (URL construction, headers) for transcription and chat calls
- Model resolution: direct (provider/model) or group reference (fallback chain)
- Fallback orchestration: try each model in order, catch errors
"""

from __future__ import annotations

import json
from typing import Any, Callable, Coroutine, List, Tuple

import httpx
from loguru import logger


class AllModelsFailedError(Exception):
    """Raised when all models in a fallback chain have failed."""

    def __init__(self, errors: List[Tuple[str, Exception]]):
        self.errors = errors

        def _fmt(e: Exception) -> str:
            msg = str(e)
            return msg if msg else f"[no details, type={type(e).__name__}]"

        error_messages = "; ".join(f"{model}: {_fmt(e)}" for model, e in errors)
        super().__init__(f"All models failed: {error_messages}")


class ProviderError(Exception):
    """Raised when a provider call fails."""

    pass


class ProviderClient:
    """Wraps a single provider's config (base_url, api_key)."""

    def __init__(self, base_url: str, api_key: str, provider_id: str = ""):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._provider_id = provider_id

    @property
    def base_url(self) -> str:
        return self._base_url

    async def post_transcription(
        self,
        client: httpx.AsyncClient,
        audio_bytes: bytes,
        model: str,
        prompt: str | None = None,
        filename: str = "audio.wav",
        timeout: float | None = None,
        model_params: dict[str, Any] | None = None,
    ) -> str:
        files = {
            "file": (filename, audio_bytes, "application/octet-stream"),
        }
        data = {"model": model, "stream": False}
        if prompt is not None:
            data["prompt"] = prompt
        if model_params:
            data.update(model_params)

        response = await client.post(
            f"{self._base_url}/audio/transcriptions",
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=timeout,
        )
        if response.is_error:
            raise httpx.HTTPStatusError(
                response.text,
                request=response.request,
                response=response,
            )
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            logger.error(
                "STT API returned non-JSON response | provider={} | model={} | status={} | body_preview={} | {}",
                self._base_url,
                model,
                response.status_code,
                (response.text or "")[:512],
                e,
                exc_info=True,
            )
            raise ProviderError(
                f"STT API '{self._base_url}' returned invalid JSON (status {response.status_code}): {e}. "
                f"Body preview: {(response.text or '')[:512]}"
            ) from e

        # Safe extraction of metadata (different providers may have different formats)
        logger.debug(
            "STT API response | provider={} | model={} | language={} | duration={}",
            self._base_url,
            data.get("model") or "N/A",
            data.get("language") or "N/A",
            data.get("duration", "N/A"),
        )

        return data["text"]

    async def post_chat(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        model: str,
        timeout: float | None = None,
        model_params: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if model_params:
            body.update(model_params)

        response = await client.post(
            f"{self._base_url}/chat/completions",
            json=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        if response.is_error:
            raise httpx.HTTPStatusError(
                response.text,
                request=response.request,
                response=response,
            )
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            logger.error(
                "Chat API returned non-JSON response | provider={} | model={} | status={} | body_preview={} | {}",
                self._base_url,
                model,
                response.status_code,
                (response.text or "")[:512],
                e,
                exc_info=True,
            )
            raise ProviderError(
                f"Chat API '{self._base_url}' returned invalid JSON (status {response.status_code}): {e}. "
                f"Body preview: {(response.text or '')[:512]}"
            ) from e

        # Safe extraction of usage metadata
        usage = data.get("usage", {})
        logger.debug(
            "Chat API response | provider={} | model={} | prompt_tokens={} | completion_tokens={} | total_tokens={}",
            self._base_url,
            data.get("model", "unknown"),
            usage.get("prompt_tokens", "N/A"),
            usage.get("completion_tokens", "N/A"),
            usage.get("total_tokens", "N/A"),
        )

        return data["choices"][0]["message"]["content"]


def resolve_model(model_field: str, app_config) -> List[Tuple[ProviderClient, str]]:
    """
    Parse the model field and return a list of (ProviderClient, model_name) tuples.

    Args:
        model_field: Either "provider_id/model_id" (direct) or "group_name" (fallback chain)
        app_config: AppConfig object with providers and model_groups

    Returns:
        List of (ProviderClient, model_name) tuples representing the fallback order

    Raises:
        ConfigError: If the model reference is invalid or referenced providers don't exist
    """
    from app.config.loader import ConfigError
    from app.config.schema import ProviderConfig

    providers = app_config.providers
    model_groups = app_config.model_groups

    if "/" in model_field:
        # Direct provider/model reference
        parts = model_field.split("/", 1)
        provider_id = parts[0]
        model_name = parts[1]

        if provider_id not in providers:
            raise ConfigError(f"Provider '{provider_id}' not found in config")

        provider = providers[provider_id]
        client = ProviderClient(
            base_url=provider.base_url,
            api_key=provider.api_key,
            provider_id=provider_id,
        )
        return [(client, model_name)]
    else:
        # Model group reference (fallback chain)
        if model_field not in model_groups:
            raise ConfigError(f"Model group '{model_field}' not found in config")

        results = []
        for entry in model_groups[model_field]:
            provider_id, model_name = entry.split("/", 1)
            if provider_id not in providers:
                raise ConfigError(
                    f"Provider '{provider_id}' (from group '{model_field}') not found"
                )

            provider = providers[provider_id]
            client = ProviderClient(
                base_url=provider.base_url,
                api_key=provider.api_key,
                provider_id=provider_id,
            )
            results.append((client, model_name))

        return results


async def call_with_fallback(
    models: List[Tuple[ProviderClient, str]],
    call_fn: Callable[[ProviderClient, str], Coroutine[Any, Any, str]],
    task_path: str = "",
) -> str:
    try:
        from app.logger import set_context

        set_context(task_path=task_path)
    except Exception:
        pass
    errors: List[Tuple[str, Exception]] = []

    for provider_client, model_name in models:
        logger.debug(
            "Trying model: {}/{}",
            provider_client._provider_id or provider_client.base_url,
            model_name,
        )
        try:
            # call_fn is awaited with provider, httpx_client, model_name
            result = await call_fn(provider_client, model_name)
            return result
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException) as e:

            def _exception_detail(exc: Exception) -> str:
                msg = str(exc)
                return msg if msg else f"[{type(exc).__name__}]"

            errors.append(
                (
                    f"{provider_client._provider_id or provider_client.base_url}/{model_name}",
                    e,
                )
            )
            logger.debug(
                "Model {}/{} failed, will try next | {}",
                provider_client._provider_id or provider_client.base_url,
                model_name,
                _exception_detail(e),
                exc_info=True,
            )
            continue
        except Exception:
            # Non-retryable errors — re-raise immediately
            raise

    def _all_errors_text(errors: List[Tuple[str, Exception]]) -> str:
        def _fmt(e: Exception) -> str:
            msg = str(e)
            return msg if msg else f"[no details, type={type(e).__name__}]"

        return "; ".join(f"{model}: {_fmt(e)}" for model, e in errors)

    logger.error(
        "All models exhausted | tried={} | {}", len(errors), _all_errors_text(errors)
    )
    raise AllModelsFailedError(errors)
