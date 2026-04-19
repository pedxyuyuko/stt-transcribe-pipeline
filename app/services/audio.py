"""Audio transcoding helpers backed by ffmpeg."""

from __future__ import annotations

import asyncio
from typing import Literal

from loguru import logger


TargetAudioFormat = Literal["wav", "mp3"]


class AudioTranscodeError(Exception):
    """Raised when audio transcoding fails."""


def get_audio_mime_type(audio_format: str) -> str:
    if audio_format == "mp3":
        return "audio/mpeg"
    if audio_format == "wav":
        return "audio/wav"
    return f"audio/{audio_format}"


def build_transcoded_filename(target_format: TargetAudioFormat) -> str:
    return f"audio.{target_format}"


async def transcode_audio(
    audio_bytes: bytes,
    source_format: str,
    target_format: TargetAudioFormat,
) -> bytes:
    output_codec_args = {
        "wav": ["-f", "wav", "-ac", "1", "-acodec", "pcm_s16le"],
        "mp3": ["-f", "mp3", "-ac", "1", "-acodec", "libmp3lame", "-q:a", "2"],
    }[target_format]

    logger.info(
        "Transcoding audio | source_format={} | target_format={} | input_size={} bytes",
        source_format,
        target_format,
        len(audio_bytes),
    )

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-v",
            "error",
            "-i",
            "pipe:0",
            "-vn",
            *output_codec_args,
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AudioTranscodeError("ffmpeg binary not found in PATH") from exc

    stdout, stderr = await process.communicate(audio_bytes)
    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        raise AudioTranscodeError(
            f"ffmpeg transcoding failed ({source_format} -> {target_format}): {stderr_text[:512]}"
        )

    logger.debug(
        "Audio transcoding finished | target_format={} | output_size={} bytes",
        target_format,
        len(stdout),
    )
    return stdout
