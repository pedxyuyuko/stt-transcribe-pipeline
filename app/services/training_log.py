"""Request-scoped training-log collection and JSON writing."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import TypeAlias, cast


AUDIO_OMITTED_MARKER = "<omitted: see audio.base64>"

_FILENAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_REPEATED_UNDERSCORE_RE = re.compile(r"_+")

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


def sanitize_filename_component(value: str | None) -> str:
    """Return a filesystem-safe filename component."""
    safe = _FILENAME_UNSAFE_RE.sub("_", value or "")
    while ".." in safe:
        safe = safe.replace("..", "_")
    safe = _REPEATED_UNDERSCORE_RE.sub("_", safe)
    safe = safe.strip("._-")
    return safe or "unknown"


def _copy_without_nested_audio(value: object) -> JsonValue:
    if isinstance(value, list):
        return [_copy_without_nested_audio(item) for item in value]

    if isinstance(value, tuple):
        return [_copy_without_nested_audio(item) for item in value]

    if isinstance(value, Mapping):
        copied: JsonObject = {
            str(key): _copy_without_nested_audio(item) for key, item in value.items()
        }
        _omit_input_audio_data(copied)
        _omit_audio_url(copied)
        return copied

    if isinstance(value, str | int | float | bool) or value is None:
        return value

    return str(value)


def _omit_input_audio_data(payload: MutableMapping[str, JsonValue]) -> None:
    input_audio = payload.get("input_audio")
    if not isinstance(input_audio, MutableMapping):
        return

    input_audio = cast(MutableMapping[str, JsonValue], input_audio)

    if isinstance(input_audio.get("data"), str):
        input_audio["data"] = AUDIO_OMITTED_MARKER


def _omit_audio_url(payload: MutableMapping[str, JsonValue]) -> None:
    audio_url = payload.get("audio_url")
    if not isinstance(audio_url, MutableMapping):
        return

    audio_url = cast(MutableMapping[str, JsonValue], audio_url)

    url = audio_url.get("url")
    if isinstance(url, str) and (";base64," in url or url.startswith("data:")):
        audio_url["url"] = AUDIO_OMITTED_MARKER


class TrainingLogCollector:
    """Collect one request's training-log payload in insertion order."""

    def __init__(
        self,
        *,
        request_id: str,
        timestamp: str,
        profile: str,
        session: str | None,
        response_format: str,
        filename: str,
        input_format: str,
        audio: bytes,
    ) -> None:
        self._request: JsonObject = {
            "id": request_id,
            "timestamp": timestamp,
            "profile": profile,
            "session": session,
            "response_format": response_format,
        }
        self._audio: JsonObject = {
            "filename": filename,
            "input_format": input_format,
            "size_bytes": len(audio),
            "base64": base64.b64encode(audio).decode("utf-8"),
            "omitted_marker": AUDIO_OMITTED_MARKER,
        }
        self._tasks: list[JsonObject] = []

    def start_task(
        self,
        *,
        block_tag: str,
        task_tag: str,
        task_type: str,
        model: str,
        block_index: int,
        task_index: int,
    ) -> "TrainingLogTaskRecorder":
        task_record: JsonObject = {
            "block": block_tag,
            "task": task_tag,
            "path": f"{block_tag}.{task_tag}",
            "type": task_type,
            "model": model,
            "block_index": block_index,
            "task_index": task_index,
            "attempts": [],
        }
        self._tasks.append(task_record)
        return TrainingLogTaskRecorder(task_record)

    def record_task(
        self,
        *,
        block_tag: str,
        task_tag: str,
        task_type: str,
        model: str,
        request: Mapping[str, object],
        response: Mapping[str, object],
    ) -> None:
        copied_request = _copy_without_nested_audio(request)
        raw_response = _copy_without_nested_audio(response)
        wrapped_response: JsonObject = {
            "raw_json": raw_response,
            "extracted": _extract_response_content(raw_response)
            if isinstance(raw_response, Mapping)
            else {},
        }
        self._tasks.append(
            {
                "block": block_tag,
                "task": task_tag,
                "path": f"{block_tag}.{task_tag}",
                "type": task_type,
                "model": model,
                "attempts": [
                    {
                        "status": "success",
                        "request": copied_request,
                        "response": wrapped_response,
                    }
                ],
                "request": copied_request,
                "response": wrapped_response,
                "extracted": wrapped_response["extracted"],
            }
        )

    def to_json(self, *, output: str) -> JsonObject:
        return {
            "request": dict(self._request),
            "audio": dict(self._audio),
            "tasks": [dict(task) for task in self._tasks],
            "output": {"text": output},
        }


class TrainingLogTaskRecorder:
    """Record provider attempts for one configured pipeline task."""

    def __init__(self, task_record: JsonObject) -> None:
        self._task_record = task_record

    def record_provider_attempt(self, **attempt: object) -> None:
        copied_attempt = _copy_without_nested_audio(attempt)
        attempt_record: JsonObject
        if isinstance(copied_attempt, dict):
            attempt_record = copied_attempt
        else:
            attempt_record = {"value": copied_attempt}

        attempts = self._task_record["attempts"]
        if not isinstance(attempts, list):
            raise TypeError("training-log task record attempts must be a list")
        attempts.append(attempt_record)

        if attempt_record.get("status") == "success":
            request = attempt_record.get("request")
            response = attempt_record.get("response")
            if isinstance(request, dict):
                self._task_record["request"] = request
            if isinstance(response, dict):
                self._task_record["response"] = response
                extracted = response.get("extracted")
                if isinstance(extracted, str | int | float | bool) or extracted is None:
                    self._task_record["extracted"] = extracted
                elif isinstance(extracted, list | dict):
                    self._task_record["extracted"] = extracted


def _extract_response_content(response: Mapping[str, JsonValue]) -> JsonObject:
    text = response.get("text")
    if isinstance(text, str):
        return {"text": text}

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, Mapping):
            message = first_choice.get("message")
            if isinstance(message, Mapping):
                content = message.get("content")
                if isinstance(content, str):
                    return {"content": content}

    return {}


class TrainingLogWriter:
    """Write training-log JSON files with safe names and atomic replacement."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path: Path = Path(path)

    def write(self, payload: Mapping[str, object]) -> Path:
        directory = Path.cwd() / self._path if not self._path.is_absolute() else self._path
        directory.mkdir(parents=True, exist_ok=True)

        output_path = self._next_available_path(directory, payload)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}-", suffix=".tmp", dir=directory
        )
        temp_path = Path(temp_name)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                _ = file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, output_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        return output_path

    def _next_available_path(self, directory: Path, payload: Mapping[str, object]) -> Path:
        base_name = self._base_filename(payload)
        candidate = directory / f"{base_name}.json"
        if not candidate.exists():
            return candidate

        suffix = 2
        while True:
            candidate = directory / f"{base_name}-{suffix}.json"
            if not candidate.exists():
                return candidate
            suffix += 1

    def _base_filename(self, payload: Mapping[str, object]) -> str:
        request = payload.get("request", {})
        if not isinstance(request, Mapping):
            request = {}

        profile = sanitize_filename_component(_string_or_none(request.get("profile")))
        session = _string_or_none(request.get("session"))
        request_id = sanitize_filename_component(_string_or_none(request.get("id")))

        parts = [profile]
        if session is not None:
            parts.append(sanitize_filename_component(session))
        parts.append(request_id)
        return "-".join(parts)


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
