"""Logging configuration and session-aware logger for the STT pipeline."""

from __future__ import annotations

import sys
import uuid
from contextvars import ContextVar

from loguru import logger

# ContextVar for session_id - async-safe, each coroutine gets its own context
_session_id_var: ContextVar[str] = ContextVar("session_id", default="no-session")


def generate_session_id() -> str:
    """Generate an 8-character short session ID."""
    return uuid.uuid4().hex[:8]


def set_session_id(sid: str) -> None:
    """Set the session ID for the current coroutine/request."""
    _session_id_var.set(sid)


def get_session_id() -> str:
    """Get the session ID for the current coroutine/request."""
    return _session_id_var.get()


def _session_id_patcher(record: dict) -> None:
    """Loguru patcher: automatically inject session_id into log extra fields."""
    record["extra"]["session_id"] = _session_id_var.get()


def setup_logging(level: str = "INFO") -> None:
    """
    Configure loguru logger.

    - Remove loguru's default handler (avoid duplicate output)
    - Add a new handler outputting to stderr
    - Log format includes time, level, session_id, message
    - Use patcher to auto-inject session_id

    Args:
        level: Log level string, e.g. "DEBUG", "INFO", etc.
    """
    # Remove loguru's default handler
    logger.remove()

    # Add new handler
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[session_id]}</cyan> | "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=False,  # Don't show variable values in production (security)
    )

    # Register patcher to auto-inject session_id
    logger.configure(patcher=_session_id_patcher)
