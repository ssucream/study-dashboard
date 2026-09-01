"""웹 auth route 실패/타임아웃 처리 테스트."""

import asyncio
from unittest.mock import patch

import pytest
from backend.api.routes import auth as auth_route
from backend.api.state import PlaybackProgress, app_state
from fastapi import HTTPException

from src import event_log


def _reset_app_state() -> None:
    app_state.scraper = None
    app_state.user_id = ""
    app_state.courses = []
    app_state.details = []
    app_state.is_playing = False
    app_state.current_lecture_title = ""
    app_state.current_lecture_url = ""
    app_state.current_week_label = ""
    app_state.current_course_name = ""
    app_state.current_course_id = ""
    app_state.playback = PlaybackProgress()
    app_state.play_task = None
    app_state.play_task_id = None
    app_state.auto.enabled = False
    app_state.auto.task = None
    app_state.auto.task_id = None


def _make_db(tmp_path):
    import src.db as db_module

    return patch.object(db_module, "_db_path", return_value=tmp_path / "app.db")


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import src.db as db_module
    from src.config import Config

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    monkeypatch.setattr(Config, "AUTO_ENABLED", "false")
    monkeypatch.setattr(Config, "AUTO_SCHEDULE_HOURS", "")
    _reset_app_state()
    yield
    _reset_app_state()


@pytest.mark.asyncio
async def test_login_invalid_credentials_returns_401(monkeypatch):
    closed = False

    class FakeScraper:
        def __init__(self, username: str, password: str):
            self.username = username
            self.password = password

        async def start(self):
            raise RuntimeError("invalid")

        async def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", FakeScraper)

    with pytest.raises(HTTPException) as exc:
        await auth_route.login(auth_route.LoginRequest(user_id="bad", password="bad"))

    assert exc.value.status_code == 401
    assert "로그인 실패" in exc.value.detail
    assert closed is True
    assert app_state.scraper is None


@pytest.mark.asyncio
async def test_login_timeout_returns_504_and_closes_scraper(monkeypatch):
    closed = False

    class FakeScraper:
        def __init__(self, username: str, password: str):
            self.username = username
            self.password = password

        async def start(self):
            await asyncio.sleep(1)

        async def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", FakeScraper)
    monkeypatch.setattr(auth_route, "_LOGIN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(HTTPException) as exc:
        await auth_route.login(auth_route.LoginRequest(user_id="slow", password="slow"))

    assert exc.value.status_code == 504
    assert "로그인 시간이 초과" in exc.value.detail
    assert closed is True
    assert app_state.scraper is None


@pytest.mark.asyncio
async def test_login_timeout_does_not_wait_for_noncooperative_start(monkeypatch):
    closed = False
    should_stop = False

    class FakeScraper:
        def __init__(self, username: str, password: str):
            self.username = username
            self.password = password

        async def start(self):
            while True:
                if should_stop:
                    return
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    # Playwright 내부 작업이 cancellation에 즉시 응답하지 않는 경우를 재현한다.
                    continue

        async def close(self):
            nonlocal closed, should_stop
            closed = True
            should_stop = True

    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", FakeScraper)
    monkeypatch.setattr(auth_route, "_LOGIN_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(HTTPException) as exc:
        await asyncio.wait_for(
            auth_route.login(auth_route.LoginRequest(user_id="stuck", password="stuck")),
            timeout=0.2,
        )

    assert exc.value.status_code == 504
    assert app_state.scraper is None
    await asyncio.sleep(0.05)
    assert closed is True


@pytest.mark.asyncio
async def test_login_and_logout_write_event_logs(monkeypatch, tmp_path):
    class FakeScraper:
        def __init__(self, username: str, password: str):
            self.username = username
            self.password = password

        async def start(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", FakeScraper)

    with _make_db(tmp_path):
        await auth_route.login(auth_route.LoginRequest(user_id="student123", password="secret"))
        await auth_route.logout()
        events = event_log.list_events(event_type="auth", limit=10)

    assert [event["action"] for event in events] == ["logout", "login"]
    assert all(event_log.is_timestamp_format(event["created_at"]) for event in events)
    assert events[1]["actor_user_id"] == event_log.mask_user_id("student123")


@pytest.mark.asyncio
async def test_logout_keeps_scraper_alive_if_task_still_running(monkeypatch):
    """cancel()이 timeout으로 포기해도, 아직 실행 중인 task가 참조할 scraper를 닫으면 안 된다."""
    from backend.api.task_manager import task_manager

    class FakeScraper:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    scraper = FakeScraper()
    app_state.scraper = scraper
    app_state.user_id = "student"
    started = asyncio.Event()

    async def factory(managed):
        try:
            started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)  # cancel()의 timeout보다 오래 걸리는 정리 작업 시뮬레이션
            raise

    managed = task_manager.create("player", factory)
    app_state.play_task_id = managed.id
    app_state.play_task = managed.task
    await started.wait()  # task가 실제로 실행을 시작한 뒤에 cancel해야 한다 (시작 전 cancel은 본문을 건너뛴다)

    orig_cancel = auth_route.task_manager.cancel

    async def fast_cancel(task_id, timeout=0.02):
        return await orig_cancel(task_id, timeout=timeout)

    monkeypatch.setattr(auth_route.task_manager, "cancel", fast_cancel)

    await auth_route.logout()

    assert scraper.closed is False
    assert app_state.scraper is scraper

    managed.task.cancel()
    await managed.task


class _FakeScraper:
    def __init__(self, username: str = "", password: str = ""):
        self.username = username
        self.password = password
        self.closed = False

    async def start(self):
        return None

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_login_resumes_persisted_auto_mode(monkeypatch):
    """백엔드 재시작 후 재로그인 시, DB에 자동 모드가 켜져 있으면 자동으로 재개한다."""
    from src.config import Config

    monkeypatch.setattr(Config, "AUTO_ENABLED", "true")
    monkeypatch.setattr(Config, "AUTO_SCHEDULE_HOURS", "7,19")
    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _FakeScraper)

    launched: list[list[int]] = []

    def fake_launch(hours):
        launched.append(hours)
        app_state.auto.enabled = True
        return "auto-task-1"

    monkeypatch.setattr("backend.api.routes.auto._launch_auto_loop", fake_launch)

    result = await auth_route.login(auth_route.LoginRequest(user_id="s", password="p"))

    assert result["success"] is True
    assert launched == [[7, 19]]
    assert app_state.auto.enabled is True


@pytest.mark.asyncio
async def test_login_does_not_resume_when_auto_persisted_disabled(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "AUTO_ENABLED", "false")
    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _FakeScraper)

    launched: list = []
    monkeypatch.setattr("backend.api.routes.auto._launch_auto_loop", lambda hours: launched.append(hours))

    await auth_route.login(auth_route.LoginRequest(user_id="s", password="p"))

    assert launched == []
    assert app_state.auto.enabled is False


@pytest.mark.asyncio
async def test_logout_keeps_persisted_auto_state(monkeypatch):
    """로그아웃은 자동 모드 지속 상태를 끄지 않는다 — 재로그인 시 자동 재개돼야 한다."""
    import src.db as db_module
    from src.config import Config

    app_state.scraper = _FakeScraper()
    Config.save_auto_state(True, [9, 13])
    assert db_module.get("AUTO_ENABLED") == "true"

    await auth_route.logout()

    assert db_module.get("AUTO_ENABLED") == "true"
    assert db_module.get("AUTO_SCHEDULE_HOURS") == "9,13"


@pytest.mark.asyncio
async def test_relogin_after_logout_resumes_auto_mode(monkeypatch):
    """로그아웃 → 재로그인 시나리오에서 자동 모드가 자동 재개된다."""
    import src.db as db_module
    from src.config import Config

    monkeypatch.setattr("src.scraper.course_scraper.CourseScraper", _FakeScraper)
    launched: list[list[int]] = []
    monkeypatch.setattr(
        "backend.api.routes.auto._launch_auto_loop",
        lambda hours: launched.append(hours) or "task-x",
    )

    # 사용자가 자동 모드를 켜고 로그아웃
    app_state.scraper = _FakeScraper()
    Config.save_auto_state(True, [9, 13])
    await auth_route.logout()
    assert db_module.get("AUTO_ENABLED") == "true"

    # Config.load()가 재시작을 시뮬레이션 — DB에서 지속 상태를 다시 읽음
    Config.load()
    assert Config.AUTO_ENABLED == "true"

    await auth_route.login(auth_route.LoginRequest(user_id="s", password="p"))

    assert launched == [[9, 13]]
