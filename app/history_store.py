"""Process-local in-memory session history store."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import time

from loguru import logger


class SessionHistoryStore:
    """Keep bounded task result history per user session and task key."""

    max_history_length: int
    session_idle_timeout_minutes: int | None
    _entries: dict[tuple[str, str], deque[str]]
    _session_locks: dict[str, asyncio.Lock]
    _last_used_at: dict[str, float]
    _index_lock: asyncio.Lock
    _session_idle_timeout_seconds: int | None
    _time_provider: Callable[[], float]

    def __init__(
        self,
        max_history_length: int = 10,
        session_idle_timeout_minutes: int | None = None,
        time_provider: Callable[[], float] | None = None,
    ):
        if max_history_length < 1:
            raise ValueError("max_history_length must be at least 1")
        if session_idle_timeout_minutes is not None and session_idle_timeout_minutes < 1:
            raise ValueError("session_idle_timeout_minutes must be a positive integer")
        self.max_history_length = max_history_length
        self.session_idle_timeout_minutes = session_idle_timeout_minutes
        self._session_idle_timeout_seconds = (
            session_idle_timeout_minutes * 60
            if session_idle_timeout_minutes is not None
            else None
        )
        self._time_provider = time_provider or time.monotonic
        self._entries = {}
        self._session_locks = {}
        self._last_used_at = {}
        self._index_lock = asyncio.Lock()

    async def append(self, user_session_id: str, task_path: str, result: str) -> list[str]:
        """Insert a completed task result at the front and keep bounded history."""
        key = self._build_key(user_session_id, task_path)
        lock = await self._get_session_lock(user_session_id)
        async with lock:
            self._expire_session_if_idle(user_session_id)
            history = self._entries.setdefault(
                key, deque(maxlen=self.max_history_length)
            )
            history.appendleft(result)
            retained_history = list(history)
            self._touch_session(user_session_id)
            logger.debug(
                "Session history append | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
                user_session_id,
                task_path,
                retained_history,
                len(retained_history),
            )
            return retained_history

    async def read(self, user_session_id: str, task_path: str) -> list[str]:
        """Read retained history for one session/task pair in newest-first order."""
        key = self._build_key(user_session_id, task_path)
        lock = await self._get_session_lock(user_session_id)
        async with lock:
            self._expire_session_if_idle(user_session_id)
            history = self._entries.get(key)
            if history is None:
                self._touch_session(user_session_id)
                logger.debug(
                    "Session history read | user_session_id={} | task_path={} | retained_history=[] | retained_length=0",
                    user_session_id,
                    task_path,
                )
                return []
            retained_history = list(history)
            self._touch_session(user_session_id)
            logger.debug(
                "Session history read | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
                user_session_id,
                task_path,
                retained_history,
                len(retained_history),
            )
            return retained_history

    async def truncate(
        self,
        user_session_id: str,
        task_path: str,
        max_history_length: int | None = None,
    ) -> list[str]:
        """Trim retained history for one session/task pair atomically."""
        retained_length = self.max_history_length
        if max_history_length is not None:
            if max_history_length < 1:
                raise ValueError("max_history_length must be at least 1")
            retained_length = max_history_length

        key = self._build_key(user_session_id, task_path)
        lock = await self._get_session_lock(user_session_id)
        async with lock:
            self._expire_session_if_idle(user_session_id)
            history = self._entries.get(key)
            if history is None:
                self._touch_session(user_session_id)
                logger.debug(
                    "Session history truncate | user_session_id={} | task_path={} | retained_history=[] | retained_length=0",
                    user_session_id,
                    task_path,
                )
                return []
            if len(history) <= retained_length:
                retained_history = list(history)
                self._touch_session(user_session_id)
                logger.debug(
                    "Session history truncate | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
                    user_session_id,
                    task_path,
                    retained_history,
                    len(retained_history),
                )
                return retained_history
            trimmed_history = deque(list(history)[:retained_length], maxlen=retained_length)
            self._entries[key] = trimmed_history
            retained_history = list(trimmed_history)
            self._touch_session(user_session_id)
            logger.debug(
                "Session history truncate | user_session_id={} | task_path={} | retained_history={} | retained_length={}",
                user_session_id,
                task_path,
                retained_history,
                len(retained_history),
            )
            return retained_history

    @staticmethod
    def _build_key(user_session_id: str, task_path: str) -> tuple[str, str]:
        return user_session_id, task_path

    async def _get_session_lock(self, user_session_id: str) -> asyncio.Lock:
        async with self._index_lock:
            lock = self._session_locks.get(user_session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_locks[user_session_id] = lock
            return lock

    def _expire_session_if_idle(self, user_session_id: str) -> None:
        if self._session_idle_timeout_seconds is None:
            return

        last_used_at = self._last_used_at.get(user_session_id)
        if last_used_at is None:
            return

        idle_seconds = self._time_provider() - last_used_at
        if idle_seconds < self._session_idle_timeout_seconds:
            return

        self._clear_session(user_session_id)
        logger.debug(
            "Session history expired | user_session_id={} | idle_seconds={} | idle_timeout_seconds={}",
            user_session_id,
            idle_seconds,
            self._session_idle_timeout_seconds,
        )

    def _clear_session(self, user_session_id: str) -> None:
        keys_to_delete = [
            key for key in self._entries.keys() if key[0] == user_session_id
        ]
        for key in keys_to_delete:
            del self._entries[key]
        _ = self._last_used_at.pop(user_session_id, None)

    def _touch_session(self, user_session_id: str) -> None:
        self._last_used_at[user_session_id] = self._time_provider()
