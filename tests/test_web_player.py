"""웹 player route 상태 반영 테스트."""

import pytest
from backend.api.routes import player as player_route
from backend.api.state import PlaybackProgress, app_state

from src import event_log
from src.player.background_player import PlaybackState
from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week


class _FakeScraper:
    _page = object()


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


def _seed_course() -> tuple[Course, LectureItem]:
    course = Course(id="42", long_name="테스트 과목", href="/courses/42", term="2026-1")
    lecture = LectureItem(
        title="1강",
        item_url="/courses/42/lecture_attendance/items/view/1",
        lecture_type=LectureType.MOVIE,
        week_label="1주차",
        completion="incomplete",
    )
    detail = CourseDetail(
        course=course,
        course_name=course.long_name,
        professors="교수",
        weeks=[Week(title="1주차", week_number=1, lectures=[lecture])],
    )
    app_state.scraper = _FakeScraper()
    app_state.user_id = "student"
    app_state.courses = [course]
    app_state.details = [detail]
    return course, lecture


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    _reset_app_state()
    yield
    _reset_app_state()


@pytest.mark.asyncio
async def test_start_play_marks_completed_lecture(monkeypatch):
    course, lecture = _seed_course()

    async def fake_play_lecture(page, lecture_url, on_progress=None, debug=False, log_fn=None):
        state = PlaybackState(current=10, duration=10, ended=True)
        if log_fn:
            log_fn("fake playback log")
        if on_progress:
            on_progress(state)
        return state

    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play_lecture)

    await player_route.start_play(
        player_route.PlayRequest(
            course_id=course.id,
            lecture_url=lecture.full_url,
            lecture_title=lecture.title,
            week_label=lecture.week_label,
        )
    )
    await app_state.play_task

    assert app_state.is_playing is False
    assert app_state.playback.status == "completed"
    assert app_state.playback.ended is True
    assert lecture.completion == "completed"
    events = event_log.list_events(event_type="player", limit=10)
    assert [event["action"] for event in events] == ["play_complete", "play_start"]
    assert events[0]["status"] == "success"
    assert event_log.is_timestamp_format(events[0]["created_at"])


@pytest.mark.asyncio
async def test_play_complete_sends_telegram_notification(monkeypatch):
    """1-A: 재생 완료 시 텔레그램 완료 알림 전송."""
    course, lecture = _seed_course()

    async def fake_play_lecture(page, lecture_url, on_progress=None, debug=False, log_fn=None):
        state = PlaybackState(current=10, duration=10, ended=True)
        if on_progress:
            on_progress(state)
        return state

    notified = {}

    def fake_notify_complete(bot_token, chat_id, course_name, week_label, lecture_title):
        notified["sent"] = True
        notified["course_name"] = course_name
        notified["lecture_title"] = lecture_title
        return True

    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play_lecture)
    monkeypatch.setattr("src.notifier.telegram_notifier.notify_playback_complete", fake_notify_complete)
    monkeypatch.setattr("src.config.Config.TELEGRAM_ENABLED", "true")
    monkeypatch.setattr("src.config.Config.TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("src.config.Config.TELEGRAM_CHAT_ID", "chat")

    await player_route.start_play(
        player_route.PlayRequest(
            course_id=course.id,
            lecture_url=lecture.full_url,
            lecture_title=lecture.title,
            week_label=lecture.week_label,
        )
    )
    await app_state.play_task

    assert notified.get("sent") is True
    assert notified["course_name"] == "테스트 과목"
    assert notified["lecture_title"] == "1강"


@pytest.mark.asyncio
async def test_play_error_sends_failed_true_notification(monkeypatch):
    """1-B: 재생 오류 시 failed=True 알림 전송."""
    course, lecture = _seed_course()

    async def fake_play_lecture(page, lecture_url, on_progress=None, debug=False, log_fn=None):
        return PlaybackState(current=2, duration=10, ended=False, error="비디오 오류")

    notified = {}

    def fake_notify_error(bot_token, chat_id, course_name, week_label, lecture_title, failed=True):
        notified["failed"] = failed
        return True

    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play_lecture)
    monkeypatch.setattr("src.notifier.telegram_notifier.notify_playback_error", fake_notify_error)
    monkeypatch.setattr(player_route, "_write_playback_log", lambda *a: None)
    monkeypatch.setattr("src.config.Config.TELEGRAM_ENABLED", "true")
    monkeypatch.setattr("src.config.Config.TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("src.config.Config.TELEGRAM_CHAT_ID", "chat")

    await player_route.start_play(
        player_route.PlayRequest(
            course_id=course.id,
            lecture_url=lecture.full_url,
            lecture_title=lecture.title,
            week_label=lecture.week_label,
        )
    )
    await app_state.play_task

    assert notified.get("failed") is True


@pytest.mark.asyncio
async def test_play_incomplete_sends_failed_false_notification(monkeypatch):
    """1-B: 재생 미완료(오류 없음) 시 failed=False 알림 전송."""
    course, lecture = _seed_course()

    async def fake_play_lecture(page, lecture_url, on_progress=None, debug=False, log_fn=None):
        return PlaybackState(current=5, duration=10, ended=False, error=None)

    notified = {}

    def fake_notify_error(bot_token, chat_id, course_name, week_label, lecture_title, failed=True):
        notified["failed"] = failed
        return True

    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play_lecture)
    monkeypatch.setattr("src.notifier.telegram_notifier.notify_playback_error", fake_notify_error)
    monkeypatch.setattr("src.config.Config.TELEGRAM_ENABLED", "true")
    monkeypatch.setattr("src.config.Config.TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("src.config.Config.TELEGRAM_CHAT_ID", "chat")

    await player_route.start_play(
        player_route.PlayRequest(
            course_id=course.id,
            lecture_url=lecture.full_url,
            lecture_title=lecture.title,
            week_label=lecture.week_label,
        )
    )
    await app_state.play_task

    assert notified.get("failed") is False


@pytest.mark.asyncio
async def test_start_play_preserves_playback_error(monkeypatch):
    course, lecture = _seed_course()

    async def fake_play_lecture(page, lecture_url, on_progress=None, debug=False, log_fn=None):
        state = PlaybackState(current=2, duration=10, ended=False, error="비디오 프레임을 찾지 못했습니다.")
        if on_progress:
            on_progress(state)
        return state

    monkeypatch.setattr("src.player.background_player.play_lecture", fake_play_lecture)
    monkeypatch.setattr(player_route, "_write_playback_log", lambda *args: "/tmp/web_play.log")

    await player_route.start_play(
        player_route.PlayRequest(
            course_id=course.id,
            lecture_url=lecture.full_url,
            lecture_title=lecture.title,
            week_label=lecture.week_label,
        )
    )
    await app_state.play_task

    status = await player_route.get_status()
    assert status["is_playing"] is False
    assert status["status"] == "error"
    assert status["error"] == "비디오 프레임을 찾지 못했습니다."
    assert status["log_path"] == "/tmp/web_play.log"
    assert status["course_id"] == course.id
    assert status["lecture_url"] == lecture.full_url
    assert lecture.completion == "incomplete"
    events = event_log.list_events(event_type="player", status="failed", limit=10)
    assert events[0]["action"] == "play_failed"
    assert events[0]["error_message"] == "비디오 프레임을 찾지 못했습니다."
    assert events[0]["log_path"] == "/tmp/web_play.log"
