import asyncio

import pytest

from app.services.audio import AudioTranscodeError, transcode_audio


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, audio_bytes: bytes) -> tuple[bytes, bytes]:
        assert audio_bytes == b"source audio"
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_transcode_audio_requests_mono_output(monkeypatch):
    captured_args: tuple[object, ...] | None = None

    async def fake_create_subprocess_exec(*args, **kwargs):
        nonlocal captured_args
        captured_args = args
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        assert kwargs["stdout"] == asyncio.subprocess.PIPE
        assert kwargs["stderr"] == asyncio.subprocess.PIPE
        return _FakeProcess(returncode=0, stdout=b"mono output", stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await transcode_audio(
        audio_bytes=b"source audio",
        source_format="m4a",
        target_format="wav",
    )

    assert result == b"mono output"
    assert captured_args is not None
    assert "-ac" in captured_args
    mono_arg_index = captured_args.index("-ac")
    assert captured_args[mono_arg_index + 1] == "1"


@pytest.mark.asyncio
async def test_transcode_audio_raises_on_ffmpeg_failure(monkeypatch):
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(returncode=1, stdout=b"", stderr=b"decode failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(AudioTranscodeError, match="decode failed"):
        await transcode_audio(
            audio_bytes=b"source audio",
            source_format="m4a",
            target_format="mp3",
        )
