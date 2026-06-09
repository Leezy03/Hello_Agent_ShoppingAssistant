"""Task status and trace management for shopping analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List, Optional, Protocol
from uuid import uuid4

from ..models.schemas import (
    ShoppingAnalysisTaskStatus,
    ShoppingReport,
    StepAttemptTrace,
    TaskTraceEvent,
)
from ..config import get_settings

try:
    from redis import Redis
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - exercised only when redis package is absent
    Redis = None
    RedisError = Exception


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStore(Protocol):
    """Storage interface shared by in-memory and Redis task stores."""

    def create_task(self) -> str:
        ...

    def get_task(self, task_id: str) -> Optional[ShoppingAnalysisTaskStatus]:
        ...

    def mark_running(self, task_id: str, message: str = "任务运行中"):
        ...

    def start_step(self, task_id: str, step_key: str, step_name: str, progress: int, message: str) -> str:
        ...

    def finish_step(
        self,
        task_id: str,
        event_id: str,
        status: str,
        message: str,
        progress: int,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        attempts: Optional[List[StepAttemptTrace]] = None,
        tool_call_count: int = 0,
    ):
        ...

    def complete_task(self, task_id: str, report: ShoppingReport, status: str = "succeeded"):
        ...

    def fail_task(self, task_id: str, error: str):
        ...

    def clear(self):
        ...


class InMemoryTaskStore:
    """Thread-safe in-memory task store.

    This is intentionally process-local. It gives the app real task status and
    trace visibility for development/demo usage; production deployment should
    back this with Redis/Postgres if multiple workers are used.
    """

    def __init__(self):
        self._tasks: Dict[str, ShoppingAnalysisTaskStatus] = {}
        self._lock = RLock()

    def create_task(self) -> str:
        task_id = str(uuid4())
        now = _now()
        with self._lock:
            self._tasks[task_id] = ShoppingAnalysisTaskStatus(
                task_id=task_id,
                status="pending",
                current_step=None,
                progress=0,
                message="任务已创建",
                created_at=now,
                updated_at=now,
            )
        return task_id

    def get_task(self, task_id: str) -> Optional[ShoppingAnalysisTaskStatus]:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.model_copy(deep=True) if task else None

    def mark_running(self, task_id: str, message: str = "任务运行中"):
        with self._lock:
            task = self._require_task(task_id)
            task.status = "running"
            task.message = message
            task.updated_at = _now()

    def start_step(self, task_id: str, step_key: str, step_name: str, progress: int, message: str) -> str:
        event_id = str(uuid4())
        now = _now()
        with self._lock:
            task = self._require_task(task_id)
            task.status = "running"
            task.current_step = step_name
            task.progress = max(task.progress, min(progress, 99))
            task.message = message
            task.updated_at = now
            task.trace.append(
                TaskTraceEvent(
                    event_id=event_id,
                    step_key=step_key,
                    step_name=step_name,
                    status="running",
                    message=message,
                    started_at=now,
                )
            )
        return event_id

    def finish_step(
        self,
        task_id: str,
        event_id: str,
        status: str,
        message: str,
        progress: int,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        attempts: Optional[List[StepAttemptTrace]] = None,
        tool_call_count: int = 0,
    ):
        now = _now()
        with self._lock:
            task = self._require_task(task_id)
            event = self._find_event(task, event_id)
            event.status = status
            event.message = message
            event.ended_at = now
            event.duration_ms = int((now - event.started_at).total_seconds() * 1000)
            event.attempts = attempts or []
            event.attempt_count = len(event.attempts)
            event.tool_call_count = tool_call_count
            event.error_type = error_type
            event.error_message = error_message
            task.progress = max(task.progress, min(progress, 99))
            task.message = message
            task.updated_at = now

    def complete_task(self, task_id: str, report: ShoppingReport, status: str = "succeeded"):
        now = _now()
        with self._lock:
            task = self._require_task(task_id)
            task.status = status
            task.current_step = None
            task.progress = 100
            task.message = "分析完成" if status == "succeeded" else "分析完成，存在降级结果"
            task.report = report
            task.completed_at = now
            task.updated_at = now

    def fail_task(self, task_id: str, error: str):
        now = _now()
        with self._lock:
            task = self._require_task(task_id)
            task.status = "failed"
            task.current_step = None
            task.message = "分析失败"
            task.error = error
            task.completed_at = now
            task.updated_at = now

    def clear(self):
        with self._lock:
            self._tasks.clear()

    def _require_task(self, task_id: str) -> ShoppingAnalysisTaskStatus:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"任务不存在: {task_id}")
        return task

    def _find_event(self, task: ShoppingAnalysisTaskStatus, event_id: str) -> TaskTraceEvent:
        for event in task.trace:
            if event.event_id == event_id:
                return event
        raise KeyError(f"Trace事件不存在: {event_id}")


class RedisTaskStore:
    """Redis-backed task store for multi-worker and multi-instance deployments."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int = 86400,
        key_prefix: str = "shopping:task",
        client: Optional["Redis"] = None,
    ):
        if Redis is None and client is None:
            raise RuntimeError("redis依赖未安装,请先安装 requirements.txt")
        self._client = client or Redis.from_url(redis_url, decode_responses=True)
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._lock_timeout_seconds = 15
        self._client.ping()

    def create_task(self) -> str:
        task_id = str(uuid4())
        now = _now()
        task = ShoppingAnalysisTaskStatus(
            task_id=task_id,
            status="pending",
            current_step=None,
            progress=0,
            message="任务已创建",
            created_at=now,
            updated_at=now,
        )
        self._save_task(task)
        return task_id

    def get_task(self, task_id: str) -> Optional[ShoppingAnalysisTaskStatus]:
        raw = self._client.get(self._key(task_id))
        if not raw:
            return None
        return ShoppingAnalysisTaskStatus.model_validate_json(raw)

    def mark_running(self, task_id: str, message: str = "任务运行中"):
        with self._task_lock(task_id):
            task = self._require_task(task_id)
            task.status = "running"
            task.message = message
            task.updated_at = _now()
            self._save_task(task)

    def start_step(self, task_id: str, step_key: str, step_name: str, progress: int, message: str) -> str:
        event_id = str(uuid4())
        now = _now()
        with self._task_lock(task_id):
            task = self._require_task(task_id)
            task.status = "running"
            task.current_step = step_name
            task.progress = max(task.progress, min(progress, 99))
            task.message = message
            task.updated_at = now
            task.trace.append(
                TaskTraceEvent(
                    event_id=event_id,
                    step_key=step_key,
                    step_name=step_name,
                    status="running",
                    message=message,
                    started_at=now,
                )
            )
            self._save_task(task)
        return event_id

    def finish_step(
        self,
        task_id: str,
        event_id: str,
        status: str,
        message: str,
        progress: int,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        attempts: Optional[List[StepAttemptTrace]] = None,
        tool_call_count: int = 0,
    ):
        now = _now()
        with self._task_lock(task_id):
            task = self._require_task(task_id)
            event = self._find_event(task, event_id)
            event.status = status
            event.message = message
            event.ended_at = now
            event.duration_ms = int((now - event.started_at).total_seconds() * 1000)
            event.attempts = attempts or []
            event.attempt_count = len(event.attempts)
            event.tool_call_count = tool_call_count
            event.error_type = error_type
            event.error_message = error_message
            task.progress = max(task.progress, min(progress, 99))
            task.message = message
            task.updated_at = now
            self._save_task(task)

    def complete_task(self, task_id: str, report: ShoppingReport, status: str = "succeeded"):
        now = _now()
        with self._task_lock(task_id):
            task = self._require_task(task_id)
            task.status = status
            task.current_step = None
            task.progress = 100
            task.message = "分析完成" if status == "succeeded" else "分析完成，存在降级结果"
            task.report = report
            task.completed_at = now
            task.updated_at = now
            self._save_task(task)

    def fail_task(self, task_id: str, error: str):
        now = _now()
        with self._task_lock(task_id):
            task = self._require_task(task_id)
            task.status = "failed"
            task.current_step = None
            task.message = "分析失败"
            task.error = error
            task.completed_at = now
            task.updated_at = now
            self._save_task(task)

    def clear(self):
        keys = list(self._client.scan_iter(match=f"{self._key_prefix}:*"))
        task_keys = [key for key in keys if not key.endswith(":lock")]
        if task_keys:
            self._client.delete(*task_keys)

    def _key(self, task_id: str) -> str:
        return f"{self._key_prefix}:{task_id}"

    def _lock_key(self, task_id: str) -> str:
        return f"{self._key(task_id)}:lock"

    def _task_lock(self, task_id: str):
        return self._client.lock(
            self._lock_key(task_id),
            timeout=self._lock_timeout_seconds,
            blocking_timeout=self._lock_timeout_seconds,
        )

    def _save_task(self, task: ShoppingAnalysisTaskStatus):
        self._client.set(
            self._key(task.task_id),
            task.model_dump_json(),
            ex=self._ttl_seconds,
        )

    def _require_task(self, task_id: str) -> ShoppingAnalysisTaskStatus:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"任务不存在: {task_id}")
        return task

    def _find_event(self, task: ShoppingAnalysisTaskStatus, event_id: str) -> TaskTraceEvent:
        for event in task.trace:
            if event.event_id == event_id:
                return event
        raise KeyError(f"Trace事件不存在: {event_id}")


class TaskTracer:
    """Small adapter used by LangGraph nodes to update task status and trace."""

    _start_progress = {
        "candidate": 5,
        "review": 25,
        "price": 25,
        "red_flag": 25,
        "report": 80,
    }
    _end_progress = {
        "candidate": 20,
        "review": 38,
        "price": 56,
        "red_flag": 74,
        "report": 95,
    }

    def __init__(self, task_id: str, store: TaskStore):
        self.task_id = task_id
        self.store = store
        self._lock = RLock()
        self._degraded = False

    def start_step(self, step_key: str, step_name: str) -> str:
        return self.store.start_step(
            task_id=self.task_id,
            step_key=step_key,
            step_name=step_name,
            progress=self._start_progress.get(step_key, 10),
            message=f"{step_name}开始执行",
        )

    def finish_step(
        self,
        event_id: str,
        step_key: str,
        step_name: str,
        ok: bool,
        message: str,
        error: Optional[Exception] = None,
        partial: bool = False,
        attempts: Optional[List[StepAttemptTrace]] = None,
        tool_call_count: int = 0,
    ):
        status = "success" if ok and not partial else "partial" if partial else "failed"
        if status != "success":
            with self._lock:
                self._degraded = True

        self.store.finish_step(
            task_id=self.task_id,
            event_id=event_id,
            status=status,
            message=message,
            progress=self._end_progress.get(step_key, 90),
            error_type=type(error).__name__ if error else None,
            error_message=str(error) if error else None,
            attempts=attempts,
            tool_call_count=tool_call_count,
        )

    def final_status(self) -> str:
        with self._lock:
            return "partial" if self._degraded else "succeeded"


def create_task_store() -> TaskStore:
    settings = get_settings()
    backend = settings.task_store_backend.lower()
    if backend == "memory":
        return InMemoryTaskStore()
    if backend == "redis":
        try:
            return RedisTaskStore(
                redis_url=settings.redis_url,
                ttl_seconds=settings.task_state_ttl_seconds,
            )
        except RedisError as exc:
            raise RuntimeError(f"Redis任务状态存储初始化失败: {exc}") from exc
    raise RuntimeError(f"不支持的任务状态存储: {settings.task_store_backend}")


task_store = create_task_store()
