"""웹 courses route (특히 refresh_courses) 테스트."""

import pytest
from backend.api.routes import courses as courses_route
from backend.api.state import PlaybackProgress, app_state
from fastapi import HTTPException

from src.scraper.models import Course


class _FakeScraper:
    _page = object()
    username = "u"
    password = "p"

    def __init__(self, courses=None, details=None, fail=False, fail_message="scrape failed"):
        self._courses = courses or []
        self._details = details or []
        self._fail = fail
        self._fail_message = fail_message
        self.closed = False

    async def close(self):
        self.closed = True

    async def fetch_courses(self):
        if self._fail:
            raise RuntimeError(self._fail_message)
        return self._courses

    async def fetch_all_details(self, courses, concurrency=3):
        if self._fail:
            raise RuntimeError(self._fail_message)
        return self._details


def _reset_app_state() -> None:
    app_state.scraper = None
    app_state.user_id = ""
    app_state.courses = []
    app_state.details = []
    app_state.is_playing = False
    app_state.playback = PlaybackProgress()
    app_state.auto.enabled = False


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    _reset_app_state()
    yield
    _reset_app_state()


@pytest.mark.asyncio
async def test_refresh_courses_rejects_while_playing():
    """회귀 테스트: 재생 중에는 새로고침이 공유 Playwright page를 건드리면 안 된다."""
    app_state.scraper = _FakeScraper()
    app_state.is_playing = True

    with pytest.raises(HTTPException) as exc:
        await courses_route.refresh_courses()

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_refresh_courses_rejects_while_auto_enabled():
    """회귀 테스트: 자동 모드 실행 중에는 새로고침이 공유 Playwright page를 건드리면 안 된다."""
    app_state.scraper = _FakeScraper()
    app_state.auto.enabled = True

    with pytest.raises(HTTPException) as exc:
        await courses_route.refresh_courses()

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_refresh_courses_preserves_state_on_scrape_failure():
    """회귀 테스트: 스크래핑 실패 시 기존 courses/details를 비워버리면 안 된다 (대시보드/미제출 항목 API가 깨짐)."""
    old_course = Course(id="1", long_name="기존 과목", href="/courses/1", term="2026-1")
    app_state.courses = [old_course]
    app_state.details = [None]
    app_state.scraper = _FakeScraper(fail=True)

    with pytest.raises(HTTPException) as exc:
        await courses_route.refresh_courses()

    assert exc.value.status_code == 503
    assert app_state.courses == [old_course]
    assert app_state.details == [None]


@pytest.mark.asyncio
async def test_refresh_courses_forces_logout_on_unrecoverable_session_loss():
    """쿠키 전용(자격증명 없는) 세션이 만료돼 자동 재로그인도 실패하면 강제 로그아웃하고 401을 낸다."""
    scraper = _FakeScraper(fail=True, fail_message="자동 재로그인 실패. 학번/비밀번호를 확인하세요.")
    scraper.username = ""
    scraper.password = ""
    app_state.scraper = scraper
    app_state.user_id = "test-user"

    with pytest.raises(HTTPException) as exc:
        await courses_route.refresh_courses()

    assert exc.value.status_code == 401
    assert app_state.scraper is None
    assert scraper.closed is True
    assert app_state.user_id == ""


@pytest.mark.asyncio
async def test_refresh_courses_replaces_state_on_success():
    new_course = Course(id="2", long_name="새 과목", href="/courses/2", term="2026-1")
    app_state.scraper = _FakeScraper(courses=[new_course], details=[None])

    result = await courses_route.refresh_courses()

    assert result == {"success": True, "count": 1}
    assert app_state.courses == [new_course]
