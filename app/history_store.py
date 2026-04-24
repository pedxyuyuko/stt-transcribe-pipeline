"""Process-local in-memory session history store."""

from __future__ import annotations

import asyncio
from collections import deque

from loguru import logger


class SessionHistoryStore:
    """Keep bounded task result history per user session and task key."""

    max_history_length: int
    _entries: dict[tuple[str, str], deque[str]]
    _locks: dict[tuple[str, str], asyncio.Lock]
    _index_lock: asyncio.Lock

    def __init__(self, max_history_length: int = 10):
        if max_history_length < 1:
            raise ValueError("max_history_length must be at least 1")
        self.max_history_length = max_history_length
        self._entries: dict[tuple[str, str], deque[str]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._index_lock = asyncio.Lock()

    async def append(self, user_session_id: str, task_path: str, result: str) -> list[str]:
        """Insert a completed task result at the front and keep bounded history."""
        key = self._build_key(user_session_id, task_path)
        lock = await self._get_lock(key)
        async with lock:
            history = self._entries.setdefault(
                key, deque(maxlen=self.max_history_length)
            )
            history.appendleft(result)
            retained_history = list(history)
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
        lock = await self._get_lock(key)
        async with lock:
            history = self._entries.get(key)
            if history is None:
                logger.debug(
                    "Session history read | user_session_id={} | task_path={} | retained_history=[] | retained_length=0",
                    user_session_id,
                    task_path,
                )
                return []
            retained_history = list(history)
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
        lock = await self._get_lock(key)
        async with lock:
            history = self._entries.get(key)
            if history is None:
                logger.debug(
                    "Session history truncate | user_session_id={} | task_path={} | retained_history=[] | retained_length=0",
                    user_session_id,
                    task_path,
                )
                return []
            if len(history) <= retained_length:
                retained_history = list(history)
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

    async def _get_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        async with self._index_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
            return lock
