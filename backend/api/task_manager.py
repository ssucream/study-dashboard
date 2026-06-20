"""공통 백그라운드 태스크 관리자.

다운로드/STT/요약/자동 모드처럼 오래 걸리는 작업을 동일한 상태 모델로
추적하기 위한 인메모리 레지스트리다. 현재 서비스는 단일 사용자 로컬
세션을 전제로 하므로 프로세스 재시작 후 복구는 제공하지 않는다.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

TaskFactory = Callable[["ManagedTask"], Awaitable[Any]]

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ManagedTask:
    id: str
    kind: str
    status: str = "queued"
    stage: str = "queued"
    message: str = ""
    progress_pct: float | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    task: asyncio.Task | None = field(default=None, repr=False)

    def update(
        self,
        *,
        status: str | None = None,
        stage: str | None = None,
        message: str | None = None,
        progress_pct: float | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if status is not None:
            self.status = status
        if stage is not None:
            self.stage = stage
        if message is not None:
            self.message = message
        if progress_pct is not None:
            self.progress_pct = max(0.0, min(100.0, progress_pct))
        if result is not None:
            self.result.update(result)
        if error is not None:
            self.error = error
        self.updated_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress_pct": self.progress_pct,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "running": bool(self.task and not self.task.done()),
        }


def _persist_task(managed: "ManagedTask") -> None:
    """완료/실패/취소된 task를 SQLite에 저장한다. 오류는 무시한다."""
    import json
    from contextlib import suppress

    with suppress(Exception):
        from src import db

        db.persist_task(
            task_id=managed.id,
            kind=managed.kind,
            status=managed.status,
            stage=managed.stage,
            message=managed.message,
            progress_pct=managed.progress_pct,
            result_json=json.dumps(managed.result, ensure_ascii=False),
            error=managed.error,
            metadata_json=json.dumps(managed.metadata, ensure_ascii=False),
            created_at=managed.created_at,
            updated_at=managed.updated_at,
        )


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, ManagedTask] = {}

    def create(
        self,
        kind: str,
        factory: TaskFactory,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ManagedTask:
        task_id = uuid.uuid4().hex
        managed = ManagedTask(id=task_id, kind=kind, metadata=metadata or {})
        self._tasks[task_id] = managed

        async def runner() -> None:
            managed.update(status="running", stage="running")
            try:
                result = await factory(managed)
                if isinstance(result, dict):
                    managed.update(result=result)
                if managed.status == "cancelling":
                    managed.update(status="cancelled", stage="cancelled", message="작업이 취소되었습니다.")
                elif managed.status not in {"cancelled", "failed"}:
                    managed.update(status="completed", stage="completed", progress_pct=100.0)
            except asyncio.CancelledError:
                managed.update(status="cancelled", stage="cancelled", message="작업이 취소되었습니다.")
            except Exception as e:
                managed.update(status="failed", stage="failed", error=str(e), message="작업이 실패했습니다.")
            finally:
                if managed.status in _TERMINAL_STATUSES:
                    _persist_task(managed)

        task = asyncio.create_task(runner(), name=f"study-helper:{kind}:{task_id}")
        managed.task = task
        return managed

    def get(self, task_id: str) -> ManagedTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[ManagedTask]:
        return sorted(self._tasks.values(), key=lambda task: task.created_at, reverse=True)

    async def cancel(self, task_id: str, timeout: float = 3.0) -> bool:
        managed = self.get(task_id)
        if not managed:
            return False
        if managed.task and not managed.task.done():
            managed.update(status="cancelling", stage="cancelling", message="작업을 중지하는 중입니다.")
            managed.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(managed.task), timeout=timeout)
            except TimeoutError:
                managed.update(message="작업 취소 요청을 보냈지만 아직 정리 중입니다.")
            except asyncio.CancelledError:
                managed.update(status="cancelled", stage="cancelled", message="작업이 취소되었습니다.")
        elif managed.status not in {"completed", "failed", "cancelled"}:
            managed.update(status="cancelled", stage="cancelled", message="작업이 취소되었습니다.")
        return True

    def clear(self) -> None:
        self._tasks.clear()

    def load_from_db(self, days: int = 7) -> int:
        """DB에 저장된 최근 N일치 완료 task를 인메모리 레지스트리에 복원한다.

        재시작 후에도 완료된 task 이력을 볼 수 있게 한다.
        이미 레지스트리에 있는 task는 덮어쓰지 않는다.
        """
        from contextlib import suppress

        with suppress(Exception):
            from src import db

            rows = db.load_tasks(limit=200)
            loaded = 0
            for row in rows:
                if row["id"] in self._tasks:
                    continue
                managed = ManagedTask(
                    id=row["id"],
                    kind=row["kind"],
                    status=row["status"],
                    stage=row["stage"],
                    message=row["message"],
                    progress_pct=row["progress_pct"],
                    result=row["result_json"] if isinstance(row["result_json"], dict) else {},
                    error=row["error"],
                    metadata=row["metadata_json"] if isinstance(row["metadata_json"], dict) else {},
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                self._tasks[managed.id] = managed
                loaded += 1
            return loaded
        return 0

    def purge_old(self, days: int = 7) -> int:
        """인메모리와 DB에서 N일 초과 완료 task를 정리한다."""
        from contextlib import suppress
        from datetime import UTC, datetime, timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()
        removed = [
            tid for tid, t in self._tasks.items() if t.status in _TERMINAL_STATUSES and t.created_at < cutoff_iso
        ]
        for tid in removed:
            del self._tasks[tid]

        db_removed = 0
        with suppress(Exception):
            from src import db

            db_removed = db.purge_old_tasks(days=days)

        return len(removed) + db_removed


task_manager = TaskManager()
