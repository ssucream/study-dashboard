"""backend.main lifespan 종료 처리 테스트."""

import asyncio
from unittest.mock import patch

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
    app_state.user_id = ""


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


# ── 백엔드 재시작 시 저장된 세션 쿠키로 자동 모드 무인 재개 ────────────────


def _make_db(tmp_path):
    import src.db as db_module

    return patch.object(db_module, "_db_path", return_value=tmp_path / "app.db")


class _FakeScraper:
    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password
        self.closed = False
        self.started_with: dict | None = None

    async def start(self, storage_state=None):
        self.started_with = storage_state

    async def fetch_courses(self):
        return []

    async def close(self):
        self.closed = True


class _ExpiredSessionScraper(_FakeScraper):
    async def start(self, storage_state=None):
        await super().start(storage_state)
        raise RuntimeError("저장된 세션이 만료되었습니다. 다시 로그인해주세요.")


@pytest.mark.asyncio
async def test_resume_auto_skipped_when_auto_disabled(monkeypatch, tmp_path):
    """AUTO_ENABLED가 꺼져 있으면 저장된 세션이 있어도 무인 재개를 시도하지 않는다."""
    from src.config import Config

    monkeypatch.setattr(Config, "AUTO_ENABLED", "false")
    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _FakeScraper)

    with _make_db(tmp_path), patch("src.crypto._KEY_PATH", tmp_path / ".secret_key"):
        Config.save_session_state("student123", {"cookies": []})
        await main_module._resume_auto_from_saved_session()

    assert app_state.scraper is None


@pytest.mark.asyncio
async def test_resume_auto_skipped_when_no_saved_session(monkeypatch, tmp_path):
    """자동 모드는 켜져 있지만 저장된 세션이 없으면(한 번도 로그인한 적 없음) 조용히 넘어간다."""
    from src.config import Config

    monkeypatch.setattr(Config, "AUTO_ENABLED", "true")
    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _FakeScraper)

    with _make_db(tmp_path):
        await main_module._resume_auto_from_saved_session()

    assert app_state.scraper is None


@pytest.mark.asyncio
async def test_resume_auto_restores_scraper_and_resumes_schedule(monkeypatch, tmp_path):
    """저장된 세션 쿠키가 아직 유효하면 로그인 없이 scraper를 복원하고 자동 모드를 재개한다."""
    from src.config import Config

    monkeypatch.setattr(Config, "AUTO_ENABLED", "true")
    monkeypatch.setattr(Config, "AUTO_SCHEDULE_HOURS", "9,13")
    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _FakeScraper)

    resumed = []
    monkeypatch.setattr("backend.api.routes.auto.resume_persisted_auto", lambda: resumed.append(True) or True)

    with _make_db(tmp_path), patch("src.crypto._KEY_PATH", tmp_path / ".secret_key"):
        Config.save_session_state("student123", {"cookies": [{"name": "sid", "value": "abc"}]})
        await main_module._resume_auto_from_saved_session()

    assert isinstance(app_state.scraper, _FakeScraper)
    assert app_state.scraper.started_with == {"cookies": [{"name": "sid", "value": "abc"}]}
    assert app_state.user_id == "student123"
    assert resumed == [True]


@pytest.mark.asyncio
async def test_resume_auto_gives_up_quietly_when_session_expired(monkeypatch, tmp_path):
    """저장된 세션이 이미 만료됐으면(자격증명이 없어 재로그인 불가) scraper를 붙이지 않고 정리한다."""
    from src.config import Config

    monkeypatch.setattr(Config, "AUTO_ENABLED", "true")
    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _ExpiredSessionScraper)

    resumed = []
    monkeypatch.setattr("backend.api.routes.auto.resume_persisted_auto", lambda: resumed.append(True))

    with _make_db(tmp_path), patch("src.crypto._KEY_PATH", tmp_path / ".secret_key"):
        Config.save_session_state("student123", {"cookies": []})
        await main_module._resume_auto_from_saved_session()

    assert app_state.scraper is None
    assert resumed == []
