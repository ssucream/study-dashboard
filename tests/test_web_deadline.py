"""웹 deadline route 테스트 — 로그인 직후 자동 마감 체크가 과목 미로드 상태에서도 동작해야 한다."""

import pytest
from backend.api.routes import deadline as deadline_route
from backend.api.state import PlaybackProgress, app_state
from fastapi import HTTPException

from src.scraper.models import Course


class _FakeScraper:
    _page = object()

    def __init__(self, courses=None, details=None, fail=False):
        self._courses = courses or []
        self._details = details or []
        self._fail = fail
        self.fetch_courses_calls = 0

    async def fetch_courses(self):
        self.fetch_courses_calls += 1
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
async def test_check_deadlines_self_loads_courses_when_empty():
    """회귀 테스트: 로그인 직후 과목 목록이 아직 없어도 409로 실패하지 않고 직접 로드한다."""
    course = Course(id="1", long_name="과목", href="/courses/1", term="2026-2")
    app_state.scraper = _FakeScraper(courses=[course], details=[None])

    res = await deadline_route.check_deadlines()

    assert res["found"] == 0
    assert app_state.courses == [course]
    assert app_state.scraper.fetch_courses_calls == 1


@pytest.mark.asyncio
async def test_check_deadlines_reuses_loaded_courses():
    """이미 로드된 경우 재스크래핑하지 않는다."""
    course = Course(id="1", long_name="과목", href="/courses/1", term="2026-2")
    app_state.scraper = _FakeScraper(courses=[course], details=[None])
    app_state.courses = [course]
    app_state.details = [None]

    await deadline_route.check_deadlines()

    assert app_state.scraper.fetch_courses_calls == 0


@pytest.mark.asyncio
async def test_check_deadlines_503_on_scrape_failure():
    """스크래핑 실패 시 503 — 프론트가 다음 로그인에 재시도할 수 있도록."""
    app_state.scraper = _FakeScraper(fail=True)

    with pytest.raises(HTTPException) as exc:
        await deadline_route.check_deadlines()

    assert exc.value.status_code == 503
