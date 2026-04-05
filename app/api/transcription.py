from __future__ import annotations

import time

from fastapi import APIRouter, Form, UploadFile, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

from app.config.schema import PipelineConfig
from app.engine.pipeline import (
    run_pipeline,
    get_pipeline_output,
    PipelineError,
    PipelineFallback,
)
from app.services.providers import AllModelsFailedError
from app.logger import generate_session_id, set_session_id, set_context

router = APIRouter()

MAX_AUDIO_SIZE = 25 * 1024 * 1024

ERROR_FILE_TOO_LARGE = {
    "error": {
        "message": "Audio file too large. Maximum size is 25MB.",
        "type": "invalid_request_error",
        "code": "file_too_large",
    }
}


def _openai_error(message: str, error_type: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": {"message": message, "type": error_type, "code": code}},
    )


async def _handle_transcription(
    request: Request,
    preset: PipelineConfig,
    preset_name: str,
    file: UploadFile,
    model: str,
    language: str | None,
    prompt: str | None,
    response_format: str,
    temperature: float | None,
):
    session_id = generate_session_id()
    set_session_id(session_id)
    set_context(preset_name=preset_name)

    audio_bytes = await file.read()

    # --- log request metadata (info level) ---
    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not client_ip:
        client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "Transcription request received | ip={} | audio_size={} bytes",
        client_ip,
        len(audio_bytes),
    )

    if len(audio_bytes) > MAX_AUDIO_SIZE:
        logger.warning("Audio file too large: {} bytes", len(audio_bytes))
        return JSONResponse(
            status_code=413,
            content=ERROR_FILE_TOO_LARGE,
        )

    # --- start timer ---
    start_time = time.monotonic()

    try:
        results = await run_pipeline(
            preset=preset,
            models_config=request.app.state.app_config,
            client=request.app.state.http_client,
            audio_bytes=audio_bytes,
        )
    except PipelineError as e:
        logger.error(
            "Pipeline error in block '{}', task '{}': {}",
            e.block_tag,
            e.task_tag,
            e.original_error,
            exc_info=True,
        )
        return _openai_error(
            message=f"Pipeline error in block '{e.block_tag}', task '{e.task_tag}': {e.original_error}",
            error_type="server_error",
            code="pipeline_error",
        )
    except PipelineFallback as e:
        elapsed = time.monotonic() - start_time
        fallback_text = e.fallback_value
        logger.warning(
            "Pipeline failed at '{}.{}, returning checkpoint fallback | elapsed={:.3f}s",
            e.failed_block,
            e.failed_task,
            elapsed,
            exc_info=True,
        )

        if response_format == "text":
            return PlainTextResponse(content=fallback_text)
        elif response_format == "verbose_json":
            return JSONResponse(
                content={
                    "text": fallback_text,
                    "pipeline_results": dict(e.results),
                    "checkpoint_fallback": True,
                }
            )
        else:
            return JSONResponse(content={"text": fallback_text})
    except AllModelsFailedError as e:
        logger.error("All models failed: {}", e, exc_info=True)
        return _openai_error(
            message=f"All models failed: {e}",
            error_type="server_error",
            code="all_models_failed",
        )

    # --- end timer + elapsed time log (info level) ---
    elapsed = time.monotonic() - start_time
    final_text = get_pipeline_output(preset.output, results)

    logger.info("Pipeline completed | elapsed={:.3f}s", elapsed)
    logger.debug("Final transcription result: {}", final_text)

    if response_format == "text":
        return PlainTextResponse(content=final_text)
    elif response_format == "verbose_json":
        return JSONResponse(
            content={
                "text": final_text,
                "pipeline_results": dict(results),
            }
        )
    else:
        return JSONResponse(content={"text": final_text})


@router.post("/v1/audio/transcriptions")
async def transcribe(
    request: Request,
    file: UploadFile,
    model: str = Form(default=""),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float | None = Form(default=None),
):
    presets: dict[str, PipelineConfig] = request.app.state.presets
    default_preset_name = request.app.state.app_config.default_preset

    if model and model in presets:
        preset_name = model
    else:
        preset_name = default_preset_name
        if model:
            logger.warning(
                "Requested model '{}' has no matching preset, falling back to '{}'",
                model,
                default_preset_name,
            )

    preset = presets[preset_name]

    return await _handle_transcription(
        request=request,
        preset=preset,
        preset_name=preset_name,
        file=file,
        model=model,
        language=language,
        prompt=prompt,
        response_format=response_format,
        temperature=temperature,
    )
