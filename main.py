from contextlib import asynccontextmanager
import os
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from uvicorn import run

from app.api import health, transcription

_SKIP_AUTH = os.environ.get("SKIP_AUTH", "").strip().lower() in ("1", "true", "yes")


def _load_server_host_port() -> tuple[str, int]:
    path = Path("config") / "config.yml"
    if not path.exists():
        path = Path("config") / "config.yml.example"
    if path.exists():
        with open(path) as f:
            cfg_data = yaml.safe_load(f)
        return str(cfg_data.get("host", "0.0.0.0")), int(cfg_data.get("port", 8000))
    return "0.0.0.0", 8000


_host, _port = _load_server_host_port()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config.loader import load_all_configs

    app_config, presets = load_all_configs(Path("config"))
    app.state.app_config = app_config
    app.state.presets = presets

    client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        timeout=httpx.Timeout(connect=10, read=120, write=30, pool=15),
    )
    app.state.http_client = client
    yield
    await client.aclose()


app = FastAPI(title="STT Transcribe Pipeline", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if _SKIP_AUTH:
        return await call_next(request)
    if request.url.path in ("/health", "/healthz", "/docs", "/openapi.json", "/redoc"):
        return await call_next(request)
    api_key = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        api_key = auth_header[7:]
    expected_key = getattr(request.app.state, "app_config", None)
    if expected_key and getattr(expected_key, "api_key", None):
        if api_key != expected_key.api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "message": "Unauthorized. Provide a valid API key via Authorization: Bearer <key>.",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    }
                },
            )
    return await call_next(request)


app.include_router(health.router)
app.include_router(transcription.router)

if __name__ == "__main__":
    run("main:app", host=_host, port=_port)
