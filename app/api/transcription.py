from __future__ import annotations

from fastapi import APIRouter, Form, UploadFile, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config.schema import PipelineConfig
from app.engine.pipeline import run_pipeline, get_pipeline_output, PipelineError
from app.services.providers import AllModelsFailedError

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
    file: UploadFile,
    model: str,
    language: str | None,
    prompt: str | None,
    response_format: str,
    temperature: float | None,
):
    audio_bytes = await file.read()
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        return JSONResponse(
            status_code=413,
            content=ERROR_FILE_TOO_LARGE,
        )

    try:
        results = await run_pipeline(
            preset=preset,
            models_config=request.app.state.app_config,
            client=request.app.state.http_client,
            audio_bytes=audio_bytes,
        )
    except PipelineError as e:
        return _openai_error(
            message=f"Pipeline error in block '{e.block_tag}', task '{e.task_tag}': {e.original_error}",
            error_type="server_error",
            code="pipeline_error",
        )
    except AllModelsFailedError as e:
        return _openai_error(
            message=f"All models failed: {e}",
            error_type="server_error",
            code="all_models_failed",
        )

    final_text = get_pipeline_output(preset.output, results)

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
async def transcribe_default(
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
    preset = presets[default_preset_name]

    return await _handle_transcription(
        request=request,
        preset=preset,
        file=file,
        model=model,
        language=language,
        prompt=prompt,
        response_format=response_format,
        temperature=temperature,
    )


@router.post("/{preset_name}/v1/audio/transcriptions")
async def transcribe_preset(
    request: Request,
    preset_name: str,
    file: UploadFile,
    model: str = Form(default=""),
    language: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float | None = Form(default=None),
):
    presets: dict[str, PipelineConfig] = request.app.state.presets

    if preset_name not in presets:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Preset '{preset_name}' not found.",
                    "type": "invalid_request_error",
                    "code": "preset_not_found",
                }
            },
        )

    preset = presets[preset_name]

    return await _handle_transcription(
        request=request,
        preset=preset,
        file=file,
        model=model,
        language=language,
        prompt=prompt,
        response_format=response_format,
        temperature=temperature,
    )
