"""Logging configuration and session-aware logger for the STT pipeline."""

from __future__ import annotations

import sys
import uuid
from contextvars import ContextVar

from loguru import logger

_session_id_var: ContextVar[str] = ContextVar("session_id", default="no-session")
_task_path_var: ContextVar[str] = ContextVar("task_path", default="")
_preset_name_var: ContextVar[str] = ContextVar("preset_name", default="")


def generate_session_id() -> str:
    return uuid.uuid4().hex[:8]


def set_session_id(sid: str) -> None:
    _session_id_var.set(sid)


def get_session_id() -> str:
    return _session_id_var.get()


def set_context(task_path: str = "", preset_name: str = "") -> None:
    if task_path:
        _task_path_var.set(task_path)
    if preset_name:
        _preset_name_var.set(preset_name)


def _context_patcher(record: dict) -> None:
    record["extra"].setdefault("session_id", _session_id_var.get())
    record["extra"].setdefault("task_path", _task_path_var.get())
    record["extra"].setdefault("preset_name", _preset_name_var.get())


def setup_logging(level: str = "INFO") -> None:
    logger.remove()

    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[session_id]}</cyan> | "
            "<blue>{file.name}:{line}</blue> | "
            "<magenta>[{extra[preset_name]}]</magenta> "
            "<green>{extra[task_path]}</green> | "
            "<level>{message}</level>"
        ),
        backtrace=True,
        diagnose=False,
    )

    logger.configure(patcher=_context_patcher)
