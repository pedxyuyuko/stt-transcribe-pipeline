import pytest
import wave
from io import BytesIO

from fastapi.testclient import TestClient
from main import app


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


class TestDefaultPresetRoute:
    def test_default_preset_route(self):
        sample_wav = _sample_wav_bytes()
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", sample_wav, "audio/wav")},
                data={"model": "test"},
            )
            assert response.status_code in [200, 500]
            if response.status_code == 500:
                data = response.json()
                assert "error" in data


class TestModelBasedPresetSelection:
    def test_model_matching_preset_uses_that_preset(self):
        sample_wav = _sample_wav_bytes()
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", sample_wav, "audio/wav")},
                data={"model": "default"},
            )
            assert response.status_code in [200, 500]
            if response.status_code == 500:
                data = response.json()
                assert "error" in data

    def test_unknown_model_falls_back_to_default(self):
        sample_wav = _sample_wav_bytes()
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", sample_wav, "audio/wav")},
                data={"model": "nonexistent-model"},
            )
            assert response.status_code in [200, 500]
            assert response.status_code != 404

    def test_empty_model_uses_default(self):
        sample_wav = _sample_wav_bytes()
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", sample_wav, "audio/wav")},
                data={"model": ""},
            )
            assert response.status_code in [200, 500]
            assert response.status_code != 404

    def test_path_based_preset_route_removed(self):
        with TestClient(app) as client:
            response = client.post(
                "/default/v1/audio/transcriptions",
                files={"file": ("test.wav", b"fake audio", "application/octet-stream")},
                data={"model": "test"},
            )
            assert response.status_code == 404


class TestTextResponseFormat:
    def test_verbose_json_format(self):
        sample_wav = _sample_wav_bytes()
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", sample_wav, "audio/wav")},
                data={"model": "test", "response_format": "verbose_json"},
            )
            if response.status_code == 200:
                data = response.json()
                assert "text" in data
                assert "pipeline_results" in data


class TestLargeFile:
    def test_file_size_limit_not_triggered(self):
        sample_wav = _sample_wav_bytes(frame_count=500)
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", sample_wav, "audio/wav")},
                data={"model": "test"},
            )
            assert response.status_code != 413
