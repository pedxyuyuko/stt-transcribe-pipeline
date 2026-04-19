FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY main.py .
COPY app/ app/

RUN mkdir -p config/presets

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "main.py"]
