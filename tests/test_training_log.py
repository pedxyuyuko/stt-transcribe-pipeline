import json
from collections.abc import Mapping
from pathlib import Path

from app.services.training_log import (
    AUDIO_OMITTED_MARKER,
    TrainingLogCollector,
    TrainingLogWriter,
    sanitize_filename_component,
)


REQUEST_ID = "18471234"
TIMESTAMP = "2026-05-16T12:34:56.789Z"
PROFILE = "default"
RESPONSE_FORMAT = "json"
UNSAFE_SESSION = "user abc/../evil"
FILENAME = "sample.wav"
INPUT_FORMAT = "wav"
AUDIO_BYTES = b"fake wav bytes"
OUTPUT_TEXT = "corrected hello world"
AUDIO_BASE64 = "ZmFrZSB3YXYgYnl0ZXM="


def _json_object(value: object) -> Mapping[str, object]:
    assert isinstance(value, dict)
    return value


def _json_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _training_log_payload(session: str | None = UNSAFE_SESSION) -> Mapping[str, object]:
    collector = TrainingLogCollector(
        request_id=REQUEST_ID,
        timestamp=TIMESTAMP,
        profile=PROFILE,
        session=session,
        response_format=RESPONSE_FORMAT,
        filename=FILENAME,
        input_format=INPUT_FORMAT,
        audio=AUDIO_BYTES,
    )
    collector.record_task(
        block_tag="stt",
        task_tag="qwen",
        task_type="transcriptions",
        model="local/qwen-audio",
        request={"model": "qwen-audio", "file": AUDIO_OMITTED_MARKER},
        response={"text": "hello world"},
    )
    collector.record_task(
        block_tag="correct",
        task_tag="final",
        task_type="chat",
        model="smart",
        request={
            "model": "gpt-4o-audio-preview",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Fix transcription"},
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": AUDIO_BASE64,
                                "format": "wav",
                            },
                        },
                        {
                            "type": "audio_url",
                            "audio_url": {
                                "url": f"data:audio/wav;base64,{AUDIO_BASE64}",
                            },
                        },
                    ],
                }
            ],
        },
        response={"choices": [{"message": {"content": OUTPUT_TEXT}}]},
    )

    return collector.to_json(output=OUTPUT_TEXT)


def test_sanitize_filename_component_removes_path_unsafe_text():
    assert sanitize_filename_component(UNSAFE_SESSION) == "user_abc_evil"


def test_writer_uses_sanitized_session_filename(tmp_path: Path):
    payload = _training_log_payload()
    writer = TrainingLogWriter(tmp_path)

    output_path = writer.write(payload)

    assert output_path.name == "default-user_abc_evil-18471234.json"
    assert output_path.parent == tmp_path
    assert output_path.exists()


def test_writer_adds_collision_suffix(tmp_path: Path):
    _ = (tmp_path / "default-18471234.json").write_text("{}")
    payload = _training_log_payload(session=None)
    writer = TrainingLogWriter(tmp_path)

    output_path = writer.write(payload)

    assert output_path.name == "default-18471234-2.json"
    assert output_path.exists()
    assert (tmp_path / "default-18471234.json").read_text() == "{}"


def test_training_log_json_schema_and_audio_payload():
    payload = _training_log_payload()

    assert set(payload) == {"request", "audio", "tasks", "output"}
    assert payload["request"] == {
        "id": REQUEST_ID,
        "timestamp": TIMESTAMP,
        "profile": PROFILE,
        "session": UNSAFE_SESSION,
        "response_format": RESPONSE_FORMAT,
    }
    audio = _json_object(payload["audio"])
    output = _json_object(payload["output"])
    tasks = _json_list(payload["tasks"])

    assert audio["filename"] == FILENAME
    assert audio["input_format"] == INPUT_FORMAT
    assert audio["size_bytes"] == len(AUDIO_BYTES)
    assert audio["base64"] == AUDIO_BASE64
    assert audio["omitted_marker"] == AUDIO_OMITTED_MARKER
    assert output == {"text": OUTPUT_TEXT}
    assert [_json_object(task)["path"] for task in tasks] == [
        "stt.qwen",
        "correct.final",
    ]
    for task in tasks:
        task_object = _json_object(task)
        assert "attempts" in task_object
        assert "provider_attempts" not in task_object
        attempts = _json_list(task_object["attempts"])
        assert len(attempts) == 1
        response = _json_object(_json_object(attempts[0])["response"])
        assert "raw_json" in response
        assert "extracted" in response
    stt_response = _json_object(_json_object(tasks[0])["response"])
    llm_response = _json_object(_json_object(tasks[1])["response"])
    assert stt_response == {
        "raw_json": {"text": "hello world"},
        "extracted": {"text": "hello world"},
    }
    assert llm_response == {
        "raw_json": {"choices": [{"message": {"content": OUTPUT_TEXT}}]},
        "extracted": {"content": OUTPUT_TEXT},
    }


def test_nested_llm_audio_payloads_are_omitted_once_audio_is_stored():
    payload = _training_log_payload()
    serialized = json.dumps(payload, sort_keys=True)
    tasks = _json_list(payload["tasks"])
    llm_task = _json_object(tasks[1])
    llm_request = _json_object(llm_task["request"])
    messages = _json_list(llm_request["messages"])
    first_message = _json_object(messages[0])
    content = _json_list(first_message["content"])
    input_audio_part = _json_object(content[1])
    input_audio = _json_object(input_audio_part["input_audio"])
    audio_url_part = _json_object(content[2])
    audio_url = _json_object(audio_url_part["audio_url"])

    assert input_audio["data"] == AUDIO_OMITTED_MARKER
    assert audio_url["url"] == AUDIO_OMITTED_MARKER
    assert serialized.count(AUDIO_BASE64) == 1


def test_writer_leaves_no_tmp_files_after_successful_write(tmp_path: Path):
    payload = _training_log_payload()
    writer = TrainingLogWriter(tmp_path)

    output_path = writer.write(payload)

    assert output_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
