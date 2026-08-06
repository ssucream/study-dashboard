"""웹 courses route (특히 refresh_courses) 테스트."""

import pytest
from backend.api.routes import courses as courses_route
from backend.api.state import PlaybackProgress, app_state
from fastapi import HTTPException

from src.scraper.models import Course


class _FakeScraper:
    _page = object()

    def __init__(self, courses=None, details=None, fail=False):
        self._courses = courses or []
        self._details = details or []
        self._fail = fail

    async def fetch_courses(self):
        if self._fail:
            raise RuntimeError("scrape failed")
        return self._courses

    async def fetch_all_details(self, courses, concurrency=3):
        if self._fail:
            raise RuntimeError("scrape failed")
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
async def test_refresh_courses_replaces_state_on_success():
    new_course = Course(id="2", long_name="새 과목", href="/courses/2", term="2026-1")
    app_state.scraper = _FakeScraper(courses=[new_course], details=[None])

    result = await courses_route.refresh_courses()

    assert result == {"success": True, "count": 1}
    assert app_state.courses == [new_course]
