"""자동 모드 route 테스트."""

import asyncio

import pytest
from backend.api.routes import auto as auto_route
from backend.api.state import PlaybackProgress, app_state, scraper_lock
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
    from src.config import Config

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    monkeypatch.setattr(Config, "AUTO_ENABLED", "false")
    monkeypatch.setattr(Config, "AUTO_SCHEDULE_HOURS", "")
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


def test_auto_cycle_shares_scraper_lock_with_course_loader():
    """자동 모드 사이클과 ensure_courses_loaded가 같은 뮤텍스를 공유해야 레이스가 없다."""
    from backend.api.routes import courses as courses_route

    assert auto_route.scraper_lock is scraper_lock
    assert courses_route._courses_load_lock is scraper_lock


@pytest.mark.asyncio
async def test_auto_cycle_scrape_waits_for_scraper_lock(monkeypatch):
    """레이스 회귀: 다른 곳이 scraper_lock을 쥐고 있으면 사이클 스크래핑이 대기하고
    app_state.details를 덮어쓰지 않는다."""
    course = Course(id="1", long_name="성서읽기", href="/courses/1", term="2026-1")
    detail = CourseDetail(course=course, course_name="성서읽기", professors="", weeks=[])

    calls = {"fetch": 0}

    class _Scraper:
        _page = object()

        async def fetch_courses(self):
            calls["fetch"] += 1
            return [course]

        async def fetch_all_details(self, courses):
            return [detail]

    app_state.scraper = _Scraper()
    app_state.auto.enabled = True
    app_state.auto.schedule_hours = [9]

    await scraper_lock.acquire()
    try:
        cycle = asyncio.create_task(auto_route._run_auto_cycle())
        await asyncio.sleep(0.05)
        assert calls["fetch"] == 0  # 락 대기 중 — 스크래핑 시작 안 함
        assert app_state.details == []
    finally:
        scraper_lock.release()

    await asyncio.wait_for(cycle, timeout=2)
    assert calls["fetch"] == 1
    assert app_state.details == [detail]


@pytest.mark.asyncio
async def test_auto_cycle_does_not_complete_lecture_when_attendance_not_recorded(monkeypatch):
    """재생은 ended=True지만 LMS 출석 미반영(error 설정)이면 완료 처리하지 않고 재시도 대상으로 남긴다."""
    course = Course(id="1", long_name="성서읽기", href="/courses/1", term="2026-1")
    lecture = _make_lecture("1주차 Intro")
    detail = CourseDetail(
        course=course,
        course_name=course.long_name,
        professors="교수",
        weeks=[Week(title="1주차", week_number=1, lectures=[lecture])],
    )

    class _Scraper:
        _page = object()

        async def close(self):
            pass

        async def start(self):
            pass

        async def fetch_courses(self):
            return [course]

        async def fetch_all_details(self, courses):
            return [detail]

    app_state.scraper = _Scraper()
    app_state.courses = [course]
    app_state.details = [detail]
    app_state.auto.enabled = True

    from src.player.background_player import PlaybackState

    async def fake_play(page, url, on_progress=None, debug=False, log_fn=None):
        return PlaybackState(
            current=1000,
            duration=1000,
            ended=True,
            progress_reported=False,
            lms_progress_ratio=0.12,
            error="재생은 끝났지만 LMS에 출석이 반영되지 않았습니다 (LMS 기록 진도 12%).",
        )

    pipeline_calls = []
    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play)
    monkeypatch.setattr(auto_route, "_run_post_play_pipeline", lambda *a, **kw: pipeline_calls.append(1) or _noop())
    monkeypatch.setattr("backend.api.routes.player._write_playback_log", lambda *a: "log/path")
    monkeypatch.setattr("backend.api.routes.player._mark_lecture_completed", lambda *a: True)

    await auto_route._run_auto_cycle()

    assert lecture.completion == "incomplete"  # 완료로 안 바뀜
    assert app_state.auto.processed_count == 0  # 처리 건수 증가 안 함
    assert pipeline_calls == []  # 다운로드/요약 파이프라인 실행 안 함
    assert app_state.playback.status == "error"


@pytest.mark.asyncio
async def test_auto_start_persists_state_to_db(monkeypatch):
    """자동 모드 시작 시 활성 상태·스케줄을 DB에 저장해 백엔드 재시작 후 복원 가능하게 한다."""
    import src.db as db_module
    from src.config import Config

    app_state.scraper = object()
    monkeypatch.setattr(auto_route, "_launch_auto_loop", lambda hours: "task-123")

    result = await auto_route.auto_start(auto_route.AutoStartRequest(schedule_hours=[8, 20]))

    assert result["started"] is True
    assert Config.AUTO_ENABLED == "true"
    assert db_module.get("AUTO_ENABLED") == "true"
    assert db_module.get("AUTO_SCHEDULE_HOURS") == "8,20"


@pytest.mark.asyncio
async def test_auto_stop_persists_disabled_state(monkeypatch):
    """자동 모드 명시적 중지 시 DB 지속 상태를 꺼서 재로그인해도 복원되지 않게 한다."""
    import src.db as db_module
    from src.config import Config

    app_state.scraper = object()
    Config.save_auto_state(True, [9, 13])

    await auto_route.auto_stop()

    assert Config.AUTO_ENABLED == "false"
    assert db_module.get("AUTO_ENABLED") == "false"


def test_get_auto_schedule_hours_parses_and_falls_back():
    from src.config import Config

    Config.AUTO_SCHEDULE_HOURS = "23,9,13"
    assert Config.get_auto_schedule_hours() == [9, 13, 23]
    Config.AUTO_SCHEDULE_HOURS = ""
    assert Config.get_auto_schedule_hours() == [9, 13, 18, 23]
    Config.AUTO_SCHEDULE_HOURS = "99,abc,-1"
    assert Config.get_auto_schedule_hours() == [9, 13, 18, 23]
