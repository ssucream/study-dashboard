"""자동 모드 route 테스트."""

import pytest
from backend.api.routes import auto as auto_route
from backend.api.state import PlaybackProgress, app_state
from backend.api.task_manager import task_manager

from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week


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
    app_state.auto.processed_count = 0
    app_state.auto.error = None
    app_state.auto.current_course = ""
    app_state.auto.current_lecture = ""
    task_manager.clear()


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    _reset_app_state()
    yield
    _reset_app_state()


def _make_lecture(title: str) -> LectureItem:
    return LectureItem(
        title=title,
        item_url=f"/courses/1/items/{title}",
        lecture_type=LectureType.MOVIE,
        week_label="1주차",
        completion="incomplete",
    )


@pytest.mark.asyncio
async def test_auto_cycle_restarts_browser_every_5_lectures(monkeypatch):
    """1-C: 5강의마다 브라우저 중간 재시작. 11강의 → idx=5, idx=10 에서 2회."""
    course = Course(id="1", long_name="테스트 과목", href="/courses/1", term="2026-1")
    lectures = [_make_lecture(f"강의{i}") for i in range(11)]
    detail = CourseDetail(
        course=course,
        course_name=course.long_name,
        professors="교수",
        weeks=[Week(title="1주차", week_number=1, lectures=lectures)],
    )

    restart_count = [0]

    class _TrackingScraper:
        _page = object()

        async def close(self):
            pass

        async def start(self):
            restart_count[0] += 1

        async def fetch_courses(self):
            return [course]

        async def fetch_all_details(self, courses):
            return [detail]

    app_state.scraper = _TrackingScraper()
    app_state.courses = [course]
    app_state.details = [detail]
    app_state.auto.enabled = True  # 루프가 break되지 않으려면 True 필요

    from src.player.background_player import PlaybackState

    async def fake_play(page, url, on_progress=None, debug=False, log_fn=None):
        return PlaybackState(current=10, duration=10, ended=True)

    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play)
    # post-play 파이프라인은 이 테스트 범위 밖 — no-op 처리
    monkeypatch.setattr(auto_route, "_run_post_play_pipeline", lambda *a, **kw: _noop())
    monkeypatch.setattr("backend.api.routes.player._write_playback_log", lambda *a: None)

    await auto_route._run_auto_cycle()

    assert restart_count[0] == 2


async def _noop(*args, **kwargs):
    pass
