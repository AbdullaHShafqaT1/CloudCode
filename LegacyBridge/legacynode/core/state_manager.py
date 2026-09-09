"""
LegacyNode — State Manager
Singleton tracking agent status, active tasks, and execution history.
Thread-safe via asyncio.Lock; persists task records to SQLite.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import aiosqlite
import structlog

log = structlog.get_logger(__name__)


class AgentStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


@dataclass
class ExecutionStep:
    """A single agent thought/action/observation cycle."""
    index: int
    thought: str
    action: Optional[str] = None
    action_input: Optional[dict] = None
    observation: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Task:
    """Represents a single coding task submitted to the agent."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    description: str = ""
    status: str = "PENDING"          # PENDING | RUNNING | COMPLETED | FAILED | CANCELLED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result_summary: Optional[str] = None
    error_message: Optional[str] = None
    step_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class StateManager:
    """
    Central state coordinator for LegacyNode.
    Maintains current agent status, task queue, live execution steps,
    and persists task history to an embedded SQLite database.
    """

    _instance: Optional["StateManager"] = None

    def __new__(cls, *args, **kwargs) -> "StateManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = ".legacynode/state.db", history_size: int = 200):
        # Guard against re-initialization on repeated __new__ calls
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._lock = asyncio.Lock()
        self._status: AgentStatus = AgentStatus.IDLE
        self._current_task: Optional[Task] = None
        self._task_queue: asyncio.Queue[Task] = asyncio.Queue()
        self._execution_steps: deque[ExecutionStep] = deque(maxlen=history_size)
        self._live_output_lines: deque[str] = deque(maxlen=500)
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db: Optional[aiosqlite.Connection] = None

        # Observers — callables notified when status changes
        self._status_observers: list[Any] = []

    # ─── Lifecycle ──────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open DB connection and create schema."""
        self._db = await aiosqlite.connect(str(self._db_path))
        await self._create_schema()
        log.info("StateManager initialized", db=str(self._db_path))

    async def close(self) -> None:
        """Close DB connection gracefully."""
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_schema(self) -> None:
        assert self._db
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                result_summary TEXT,
                error_message TEXT,
                step_count INTEGER DEFAULT 0
            )
        """)
        await self._db.commit()

    # ─── Status ─────────────────────────────────────────────────────────────────

    @property
    def status(self) -> AgentStatus:
        return self._status

    async def set_status(self, new_status: AgentStatus) -> None:
        async with self._lock:
            old_status = self._status
            self._status = new_status
            log.info("Agent status changed", old=old_status, new=new_status)
            for observer in self._status_observers:
                try:
                    await observer(old_status, new_status)
                except Exception:
                    log.warning("Status observer raised exception", exc_info=True)

    def add_status_observer(self, callback) -> None:
        """Register an async callable(old_status, new_status) to be notified on state changes."""
        self._status_observers.append(callback)

    # ─── Task Queue ─────────────────────────────────────────────────────────────

    async def enqueue_task(self, description: str) -> Task:
        """Create and enqueue a new task. Returns the Task object."""
        task = Task(description=description)
        await self._task_queue.put(task)
        await self._persist_task(task)
        log.info("Task enqueued", task_id=task.id, description=description[:80])
        return task

    async def dequeue_task(self) -> Task:
        """Block until a task is available, then return it."""
        return await self._task_queue.get()

    def task_queue_size(self) -> int:
        return self._task_queue.qsize()

    @property
    def current_task(self) -> Optional[Task]:
        return self._current_task

    async def set_current_task(self, task: Task) -> None:
        async with self._lock:
            self._current_task = task
            task.status = "RUNNING"
            task.started_at = datetime.now(timezone.utc).isoformat()
            await self._update_task(task)

    async def complete_task(self, task: Task, summary: str) -> None:
        async with self._lock:
            task.status = "COMPLETED"
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.result_summary = summary
            await self._update_task(task)
            self._current_task = None
            log.info("Task completed", task_id=task.id, summary=summary[:80])

    async def fail_task(self, task: Task, error: str) -> None:
        async with self._lock:
            task.status = "FAILED"
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.error_message = error
            await self._update_task(task)
            self._current_task = None
            log.error("Task failed", task_id=task.id, error=error[:120])

    async def cancel_current_task(self) -> None:
        async with self._lock:
            if self._current_task:
                self._current_task.status = "CANCELLED"
                self._current_task.completed_at = datetime.now(timezone.utc).isoformat()
                await self._update_task(self._current_task)
                log.info("Task cancelled", task_id=self._current_task.id)
                self._current_task = None

    # ─── Execution Steps ────────────────────────────────────────────────────────

    def add_execution_step(self, step: ExecutionStep) -> None:
        self._execution_steps.append(step)
        if self._current_task:
            self._current_task.step_count = len(self._execution_steps)

    def get_execution_steps(self) -> list[ExecutionStep]:
        return list(self._execution_steps)

    def add_live_output(self, line: str) -> None:
        """Append a line to the live terminal output buffer."""
        self._live_output_lines.append(line)

    def get_live_output(self) -> list[str]:
        return list(self._live_output_lines)

    def clear_execution_state(self) -> None:
        """Clear steps and live output for a fresh task run."""
        self._execution_steps.clear()
        self._live_output_lines.clear()

    # ─── Task History (from DB) ──────────────────────────────────────────────────

    async def get_task_history(self, limit: int = 50) -> list[dict]:
        if not self._db:
            return []
        cursor = await self._db.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    # ─── SQLite Helpers ──────────────────────────────────────────────────────────

    async def _persist_task(self, task: Task) -> None:
        if not self._db:
            return
        d = task.to_dict()
        await self._db.execute(
            """INSERT INTO tasks
               (id, description, status, created_at, started_at, completed_at,
                result_summary, error_message, step_count)
               VALUES (:id, :description, :status, :created_at, :started_at,
                       :completed_at, :result_summary, :error_message, :step_count)""",
            d,
        )
        await self._db.commit()

    async def _update_task(self, task: Task) -> None:
        if not self._db:
            return
        d = task.to_dict()
        await self._db.execute(
            """UPDATE tasks SET status=:status, started_at=:started_at,
               completed_at=:completed_at, result_summary=:result_summary,
               error_message=:error_message, step_count=:step_count
               WHERE id=:id""",
            d,
        )
        await self._db.commit()

    # ─── Snapshot for UI ────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a JSON-serializable snapshot for the Streamlit dashboard."""
        return {
            "status": self._status.value,
            "current_task": self._current_task.to_dict() if self._current_task else None,
            "queue_size": self._task_queue.qsize(),
            "step_count": len(self._execution_steps),
            "live_output": list(self._live_output_lines)[-50:],
        }
