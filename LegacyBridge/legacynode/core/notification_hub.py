"""
LegacyNode — Notification Hub
In-memory circular buffer + SQLite persistence for agent alerts.
Supports push, query, and acknowledge operations.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

import aiosqlite
import structlog

log = structlog.get_logger(__name__)


class NotificationLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class Notification:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    level: NotificationLevel = NotificationLevel.INFO
    title: str = ""
    message: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acknowledged: bool = False
    task_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level"] = self.level.value
        return d


class NotificationHub:
    """
    Central notification broker.

    Usage:
        hub = NotificationHub(db_path=".legacynode/notifications.db", max_memory=100)
        await hub.initialize()
        await hub.push(NotificationLevel.ERROR, "Build Failed", "pytest returned exit code 1")
        unread = await hub.get_unread()
    """

    def __init__(
        self,
        db_path: str = ".legacynode/notifications.db",
        max_memory: int = 100,
        retention_days: int = 7,
    ):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory: deque[Notification] = deque(maxlen=max_memory)
        self._retention_days = retention_days
        self._lock = asyncio.Lock()
        self._db: Optional[aiosqlite.Connection] = None

        # Pub-sub: list of async callbacks(notification) registered by UI
        self._subscribers: list = []

    # ─── Lifecycle ──────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._create_schema()
        await self._purge_old()
        await self._load_recent_into_memory()
        log.info("NotificationHub initialized", db=str(self._db_path))

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_schema(self) -> None:
        assert self._db
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                level TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                task_id TEXT
            )
        """)
        await self._db.commit()

    async def _purge_old(self) -> None:
        """Remove notifications older than retention_days."""
        if not self._db:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).isoformat()
        await self._db.execute("DELETE FROM notifications WHERE timestamp < ?", (cutoff,))
        await self._db.commit()

    async def _load_recent_into_memory(self) -> None:
        """Seed in-memory deque from the most recent DB records."""
        if not self._db:
            return
        cursor = await self._db.execute(
            "SELECT id, level, title, message, timestamp, acknowledged, task_id "
            "FROM notifications ORDER BY timestamp DESC LIMIT ?",
            (self._memory.maxlen,),
        )
        rows = await cursor.fetchall()
        for row in reversed(rows):
            n = Notification(
                id=row[0],
                level=NotificationLevel(row[1]),
                title=row[2],
                message=row[3],
                timestamp=row[4],
                acknowledged=bool(row[5]),
                task_id=row[6],
            )
            self._memory.append(n)

    # ─── Public API ─────────────────────────────────────────────────────────────

    async def push(
        self,
        level: NotificationLevel,
        title: str,
        message: str,
        task_id: Optional[str] = None,
    ) -> Notification:
        """
        Create and store a new notification.
        Notifies all registered subscribers.
        """
        n = Notification(level=level, title=title, message=message, task_id=task_id)
        async with self._lock:
            self._memory.append(n)
            await self._persist(n)

        log_level_str = level.value
        if level in (NotificationLevel.INFO, NotificationLevel.SUCCESS):
            log.info("Notification pushed", notify_level=log_level_str, title=title)
        else:
            log.warning("Notification pushed", notify_level=log_level_str, title=title)

        for sub in self._subscribers:
            try:
                await sub(n)
            except Exception:
                log.warning("Subscriber raised exception on notification", exc_info=True)

        return n

    async def get_all(self, limit: int = 100) -> list[Notification]:
        """Return the most recent N notifications from memory."""
        async with self._lock:
            items = list(self._memory)
        return list(reversed(items))[:limit]

    async def get_unread(self) -> list[Notification]:
        """Return all un-acknowledged notifications."""
        async with self._lock:
            return [n for n in self._memory if not n.acknowledged]

    async def acknowledge(self, notification_id: str) -> bool:
        """Mark a notification as read. Returns True if found."""
        async with self._lock:
            for n in self._memory:
                if n.id == notification_id:
                    n.acknowledged = True
                    await self._mark_acknowledged_in_db(notification_id)
                    return True
        return False

    async def acknowledge_all(self) -> int:
        """Mark all notifications as read. Returns count."""
        count = 0
        async with self._lock:
            for n in self._memory:
                if not n.acknowledged:
                    n.acknowledged = True
                    count += 1
        if count and self._db:
            await self._db.execute("UPDATE notifications SET acknowledged = 1 WHERE acknowledged = 0")
            await self._db.commit()
        return count

    def unread_count(self) -> int:
        return sum(1 for n in self._memory if not n.acknowledged)

    def subscribe(self, callback) -> None:
        """Register an async callback(notification: Notification) for real-time alerts."""
        self._subscribers.append(callback)

    # ─── DB Helpers ─────────────────────────────────────────────────────────────

    async def _persist(self, n: Notification) -> None:
        if not self._db:
            return
        await self._db.execute(
            "INSERT INTO notifications (id, level, title, message, timestamp, acknowledged, task_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (n.id, n.level.value, n.title, n.message, n.timestamp, int(n.acknowledged), n.task_id),
        )
        await self._db.commit()

    async def _mark_acknowledged_in_db(self, notification_id: str) -> None:
        if not self._db:
            return
        await self._db.execute(
            "UPDATE notifications SET acknowledged = 1 WHERE id = ?", (notification_id,)
        )
        await self._db.commit()
