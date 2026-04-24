import wave
from io import BytesIO

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from main import app
from app.api import transcription as transcription_module


def _sample_wav_bytes(frame_count: int = 160) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * frame_count)
    return buffer.getvalue()


class TestHealthEndpoint:
    def test_health_returns_ok(self):
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}


class TestTranscriptionModelParsing:
    def test_model_matching_preset_uses_that_preset(self, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_handle_transcription(**kwargs):
            captured.update(kwargs)
            return JSONResponse(content={"text": "ok"})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["preset_name"] == "default"
        assert captured["user_session_id"] is None
        assert captured["model"] == "default"

    def test_model_with_session_routes_preset_and_threads_user_session(
        self, monkeypatch
    ):
        captured: dict[str, object] = {}

        async def fake_handle_transcription(**kwargs):
            captured.update(kwargs)
            return JSONResponse(content={"text": "ok"})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/user-123"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["preset_name"] == "default"
        assert captured["user_session_id"] == "user-123"
        assert captured["model"] == "default/user-123"

    def test_model_with_session_threads_user_session_into_run_pipeline(
        self, monkeypatch
    ):
        captured: dict[str, object] = {}

        async def fake_run_pipeline(**kwargs):
            captured.update(kwargs)
            return {"stt.qwen": "ok"}

        monkeypatch.setattr(transcription_module, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(
            transcription_module, "get_pipeline_output", lambda output, results: "ok"
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/user-123"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["user_session_id"] == "user-123"

    def test_unknown_valid_preset_name_falls_back_to_default(self, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_handle_transcription(**kwargs):
            captured.update(kwargs)
            return JSONResponse(content={"text": "ok"})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "nonexistent-model"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["preset_name"] == app.state.app_config.default_preset
        assert captured["user_session_id"] is None
        assert captured["model"] == "nonexistent-model"

    def test_unknown_valid_preset_with_session_still_falls_back_to_default(
        self, monkeypatch
    ):
        captured: dict[str, object] = {}

        async def fake_handle_transcription(**kwargs):
            captured.update(kwargs)
            return JSONResponse(content={"text": "ok"})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "nonexistent-model/user-123"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["preset_name"] == app.state.app_config.default_preset
        assert captured["user_session_id"] == "user-123"
        assert captured["model"] == "nonexistent-model/user-123"

    def test_empty_model_uses_default(self, monkeypatch):
        captured: dict[str, object] = {}

        async def fake_handle_transcription(**kwargs):
            captured.update(kwargs)
            return JSONResponse(content={"text": "ok"})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": ""},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["preset_name"] == app.state.app_config.default_preset
        assert captured["user_session_id"] is None
        assert captured["model"] == ""

    def test_malformed_model_with_missing_preset_is_rejected(self):
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "/session"},
            )

        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "message": "Invalid model value. Expected 'preset_id' or 'preset_id/session_id'.",
                "type": "invalid_request_error",
                "code": "invalid_model",
            }
        }

    def test_malformed_model_with_missing_session_is_rejected(self):
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/"},
            )

        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "message": "Invalid model value. Expected 'preset_id' or 'preset_id/session_id'.",
                "type": "invalid_request_error",
                "code": "invalid_model",
            }
        }

    def test_malformed_model_with_whitespace_only_session_is_rejected(self):
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/   "},
            )

        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "message": "Invalid model value. Expected 'preset_id' or 'preset_id/session_id'.",
                "type": "invalid_request_error",
                "code": "invalid_model",
            }
        }

    def test_malformed_model_with_extra_separator_content_is_rejected(self):
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/user-123/extra"},
            )

        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "message": "Invalid model value. Expected 'preset_id' or 'preset_id/session_id'.",
                "type": "invalid_request_error",
                "code": "invalid_model",
            }
        }

    def test_path_based_preset_route_removed(self):
        with TestClient(app) as client:
            response = client.post(
                "/default/v1/audio/transcriptions",
                files={"file": ("test.wav", b"fake audio", "application/octet-stream")},
                data={"model": "test"},
            )

        assert response.status_code == 404


class TestResponseFormatAndFileLimits:
    def test_verbose_json_format(self, monkeypatch):
        async def fake_handle_transcription(**kwargs):
            assert kwargs["response_format"] == "verbose_json"
            return JSONResponse(content={"text": "ok", "pipeline_results": {"stt.qwen": "ok"}})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "test", "response_format": "verbose_json"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "text": "ok",
            "pipeline_results": {"stt.qwen": "ok"},
        }

    def test_file_size_limit_not_triggered(self, monkeypatch):
        async def fake_handle_transcription(**kwargs):
            return JSONResponse(content={"text": "ok"})

        monkeypatch.setattr(
            transcription_module, "_handle_transcription", fake_handle_transcription
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(frame_count=500), "audio/wav")},
                data={"model": "test"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}


class TestSessionAwareDegradation:
    """Endpoint-level coverage for no-session degradation with session-aware presets.

    These tests prove the endpoint remains predictable when session-aware config
    (require_session messages, record settings) is present but no user session
    is supplied. The runtime should skip require_session messages and skip
    recording gracefully — not crash.
    """

    def test_no_session_degradation_with_session_aware_preset(self, monkeypatch):
        """Preset has require_session messages + record config, but no session
        is provided. Endpoint still returns 200 with valid text."""
        captured: dict[str, object] = {}

        async def fake_run_pipeline(**kwargs):
            captured.update(kwargs)
            # Simulates pipeline completing after skipping require_session messages
            return {"stt.transcribe": "transcribed text"}

        monkeypatch.setattr(transcription_module, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(
            transcription_module, "get_pipeline_output", lambda output, results: "transcribed text"
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default"},
            )

        # Endpoint succeeds even though preset may have session-aware config
        assert response.status_code == 200
        assert response.json() == {"text": "transcribed text"}
        # No user_session_id is forwarded when model has no session component
        assert captured["user_session_id"] is None

    def test_no_session_with_preset_session_path_returns_valid_response(
        self, monkeypatch
    ):
        """When model includes a session ID, user_session_id is forwarded to
        run_pipeline even if no session history store is involved."""
        captured: dict[str, object] = {}

        async def fake_run_pipeline(**kwargs):
            captured.update(kwargs)
            return {"stt.transcribe": "ok"}

        monkeypatch.setattr(transcription_module, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(
            transcription_module, "get_pipeline_output", lambda output, results: "ok"
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/session-abc"},
            )

        assert response.status_code == 200
        assert response.json() == {"text": "ok"}
        assert captured["user_session_id"] == "session-abc"


class TestVerboseJsonWithSessionAware:
    """Coverage for verbose_json response behavior when session-aware features
    are involved — verifying response shape beyond the generic fake-only case."""

    def test_verbose_json_includes_pipeline_results_with_session(self, monkeypatch):
        """verbose_json with a session ID returns pipeline_results alongside text,
        confirming the response shape is correct when session-aware features are active."""
        captured: dict[str, object] = {}

        async def fake_run_pipeline(**kwargs):
            captured.update(kwargs)
            return {"stt.transcribe": "transcribed", "correction.fix": "corrected"}

        monkeypatch.setattr(transcription_module, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(
            transcription_module,
            "get_pipeline_output",
            lambda output, results: "corrected",
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/user-42", "response_format": "verbose_json"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["text"] == "corrected"
        assert "pipeline_results" in body
        assert body["pipeline_results"] == {
            "stt.transcribe": "transcribed",
            "correction.fix": "corrected",
        }
        # Verify session ID was forwarded
        assert captured["user_session_id"] == "user-42"

    def test_verbose_json_without_session_still_includes_pipeline_results(
        self, monkeypatch
    ):
        """verbose_json without a session ID (preset-only model) still includes
        pipeline_results — no regression in response shape."""
        async def fake_run_pipeline(**kwargs):
            assert kwargs["user_session_id"] is None
            return {"stt.transcribe": "result"}

        monkeypatch.setattr(transcription_module, "run_pipeline", fake_run_pipeline)
        monkeypatch.setattr(
            transcription_module,
            "get_pipeline_output",
            lambda output, results: "result",
        )

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default", "response_format": "verbose_json"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["text"] == "result"
        assert "pipeline_results" in body
        assert body["pipeline_results"] == {"stt.transcribe": "result"}

    def test_verbose_json_with_fallback_preserves_pipeline_results(self, monkeypatch):
        """When PipelineFallback is raised (checkpoint fallback), verbose_json
        includes checkpoint_fallback flag alongside pipeline_results."""
        from app.engine.pipeline import PipelineFallback

        async def fake_run_pipeline(**kwargs):
            raise PipelineFallback(
                failed_block="correction",
                failed_task="fix",
                fallback_value="stt fallback text",
                results={"stt.transcribe": "original stt"},
                original_error=Exception("correction failed"),
            )

        monkeypatch.setattr(transcription_module, "run_pipeline", fake_run_pipeline)

        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", _sample_wav_bytes(), "audio/wav")},
                data={"model": "default/user-session", "response_format": "verbose_json"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["text"] == "stt fallback text"
        assert body["checkpoint_fallback"] is True
        assert "pipeline_results" in body
        assert body["pipeline_results"] == {"stt.transcribe": "original stt"}
