from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from uvicorn import run

from app.api import health, transcription


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.config.loader import load_all_configs

    app_config, models_config, presets = load_all_configs(Path("config"))
    app.state.app_config = app_config
    app.state.models_config = models_config
    app.state.presets = presets

    client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        timeout=httpx.Timeout(connect=10, read=120, write=30, pool=15),
    )
    app.state.http_client = client
    yield
    await client.aclose()


app = FastAPI(title="STT Transcribe Pipeline", version="0.1.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(transcription.router)

if __name__ == "__main__":
    run("main:app", host="0.0.0.0", port=8000)
