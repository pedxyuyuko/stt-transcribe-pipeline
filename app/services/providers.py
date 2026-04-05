"""
HTTP client manager and provider abstraction for the STT pipeline.

Handles:
- Provider client (URL construction, headers) for transcription and chat calls
- Model resolution: direct (provider/model) or group reference (fallback chain)
- Fallback orchestration: try each model in order, catch errors
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine, List, Tuple

import httpx


class AllModelsFailedError(Exception):
    """Raised when all models in a fallback chain have failed."""

    def __init__(self, errors: List[Tuple[str, Exception]]):
        self.errors = errors
        error_messages = "; ".join(f"{model}: {e}" for model, e in errors)
        super().__init__(f"All models failed: {error_messages}")


class ProviderError(Exception):
    """Raised when a provider call fails."""

    pass


# Re-export ModelsConfig and ProviderConfig for type hints
def _get_models_config():
    """Lazy import to avoid circular dependency."""
    from app.config.schema import ModelsConfig, ProviderConfig

    return ModelsConfig, ProviderConfig


class ProviderClient:
    """Wraps a single provider's config (base_url, api_key)."""

    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

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
    ) -> str:
        """
        POST to {base_url}/audio/transcriptions as multipart form.
        Returns response.json()["text"].
        """
        files = {
            "file": (filename, audio_bytes, "application/octet-stream"),
        }
        data = {"model": model}
        if prompt is not None:
            data["prompt"] = prompt

        response = await client.post(
            f"{self._base_url}/audio/transcriptions",
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        response.raise_for_status()
        return response.json()["text"]

    async def post_chat(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, Any]],
        model: str,
    ) -> str:
        """
        POST to {base_url}/chat/completions as JSON.
        Returns response.json()["choices"][0]["message"]["content"].
        """
        response = await client.post(
            f"{self._base_url}/chat/completions",
            json={
                "model": model,
                "messages": messages,
            },
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


def resolve_model(model_field: str, models_config) -> List[Tuple[ProviderClient, str]]:
    """
    Parse the model field and return a list of (ProviderClient, model_name) tuples.

    Args:
        model_field: Either "provider_id/model_id" (direct) or "group_name" (fallback chain)
        models_config: ModelsConfig object

    Returns:
        List of (ProviderClient, model_name) tuples representing the fallback order

    Raises:
        ConfigError: If the model reference is invalid or referenced providers don't exist
    """
    from app.config.loader import ConfigError

    ModelsConfig, ProviderConfig = _get_models_config()
    providers = models_config.providers
    model_groups = models_config.model_groups

    if "/" in model_field:
        # Direct provider/model reference
        parts = model_field.split("/", 1)
        provider_id = parts[0]
        model_name = parts[1]

        if provider_id not in providers:
            raise ConfigError(f"Provider '{provider_id}' not found in models config")

        provider = providers[provider_id]
        client = ProviderClient(base_url=provider.base_url, api_key=provider.api_key)
        return [(client, model_name)]
    else:
        # Model group reference (fallback chain)
        if model_field not in model_groups:
            raise ConfigError(f"Model group '{model_field}' not found in models config")

        results = []
        for entry in model_groups[model_field]:
            provider_id, model_name = entry.split("/", 1)
            if provider_id not in providers:
                raise ConfigError(
                    f"Provider '{provider_id}' (from group '{model_field}') not found"
                )

            provider = providers[provider_id]
            client = ProviderClient(
                base_url=provider.base_url, api_key=provider.api_key
            )
            results.append((client, model_name))

        return results


async def call_with_fallback(
    models: List[Tuple[ProviderClient, str]],
    call_fn: Callable[[ProviderClient, str], Coroutine[Any, Any, str]],
) -> str:
    """
    Try each model in order. Catch httpx.HTTPStatusError (5xx) and httpx.ConnectError,
    try next. Raise AllModelsFailedError if all fail.

    Args:
        models: List of (ProviderClient, model_name) tuples
        call_fn: Async callable that takes (provider_client, model_name) and returns str

    Returns:
        The result string from the first successful call

    Raises:
        AllModelsFailedError: If all models in the chain fail
    """
    errors: List[Tuple[str, Exception]] = []

    for provider_client, model_name in models:
        try:
            # call_fn is awaited with provider, httpx_client, model_name
            result = await call_fn(provider_client, model_name)
            return result
        except (httpx.HTTPStatusError, httpx.ConnectError) as e:
            errors.append((f"{provider_client.base_url}/{model_name}", e))
            continue
        except Exception:
            # Non-retryable errors — re-raise immediately
            raise

    raise AllModelsFailedError(errors)
