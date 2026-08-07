"""backend.main lifespan 종료 처리 테스트."""

import asyncio

import pytest
from backend import main as main_module
from backend.api.state import app_state
from backend.api.task_manager import task_manager


@pytest.fixture(autouse=True)
def clear_tasks():
    task_manager.clear()
    yield
    task_manager.clear()
    app_state.scraper = None


@pytest.mark.asyncio
async def test_lifespan_shutdown_cancels_running_tasks(monkeypatch, tmp_path):
    """docker compose down 등으로 서버가 종료될 때 실행 중이던 task를 취소해 부분 상태로 방치하지 않는다."""
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")

    started = asyncio.Event()

    async def factory(managed):
        started.set()
        await asyncio.sleep(10)

    managed = task_manager.create("download", factory)
    await started.wait()

    async with main_module.lifespan(main_module.app):
        pass

    assert managed.status == "cancelled"
    assert managed.task.done()
