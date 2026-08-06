"""웹 다운로드 task route 테스트."""

import pytest
from backend.api.routes import tasks as tasks_route
from backend.api.state import PlaybackProgress, app_state
from backend.api.task_manager import task_manager
from fastapi import HTTPException

from src import event_log
from src.config import Config
from src.scraper.models import Course


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
    task_manager.clear()


def _seed_course() -> Course:
    course = Course(id="42", long_name="테스트 과목", href="/courses/42", term="2026-1")
    app_state.scraper = _FakeScraper()
    app_state.user_id = "student"
    app_state.courses = [course]
    return course


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    _reset_app_state()
    Config.DOWNLOAD_DIR = str(tmp_path)
    Config.DOWNLOAD_ENABLED = "true"
    Config.DOWNLOAD_RULE = "mp4"
    Config.STT_ENABLED = "false"
    Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = "false"
    Config.WHISPER_MODEL = "base"
    Config.STT_LANGUAGE = "ko"
    Config.AI_ENABLED = "false"
    Config.AI_AGENT = "gemini"
    Config.GOOGLE_API_KEY = ""
    Config.GEMINI_MODEL = ""
    Config.SUMMARY_PROMPT_EXTRA = ""
    Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = "false"
    yield
    _reset_app_state()


@pytest.mark.asyncio
async def test_start_download_creates_managed_task(monkeypatch, tmp_path):
    course = _seed_course()
    called = {}

    async def fake_download_lecture_media(**kwargs):
        called.update(kwargs)
        kwargs["on_stage"]("downloading", "테스트 다운로드 중", 50)
        return {
            "download_rule": kwargs["rule"],
            "download_dir": kwargs["download_dir"],
            "files": [{"type": "mp4", "path": str(tmp_path / "lecture.mp4")}],
        }

    monkeypatch.setattr("src.downloader.pipeline.download_lecture_media", fake_download_lecture_media)

    response = await tasks_route.start_download(
        tasks_route.DownloadTaskRequest(
            course_id=course.id,
            lecture_url="https://canvas.ssu.ac.kr/courses/42/items/1",
            lecture_title="1강",
            week_label="1주차",
        )
    )
    managed = task_manager.get(response["task_id"])
    await managed.task

    assert response["started"] is True
    assert managed.kind == "download"
    assert managed.status == "completed"
    assert managed.result["download_rule"] == "mp4"
    assert managed.result["files"][0]["type"] == "mp4"
    assert called["course_name"] == "테스트 과목"
    events = event_log.list_events(event_type="download", limit=10)
    assert [event["action"] for event in events] == ["download_complete", "download_start"]
    assert events[0]["status"] == "success"
    assert event_log.is_timestamp_format(events[0]["created_at"])


@pytest.mark.asyncio
async def test_start_download_passes_stt_options_and_logs_completion(monkeypatch, tmp_path):
    course = _seed_course()
    Config.DOWNLOAD_RULE = "mp3"
    Config.STT_ENABLED = "true"
    Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = "true"
    Config.WHISPER_MODEL = "tiny"
    Config.STT_LANGUAGE = ""
    called = {}

    async def fake_download_lecture_media(**kwargs):
        called.update(kwargs)
        kwargs["on_stage"]("transcribing", "STT 변환 중입니다.", 95)
        return {
            "download_rule": kwargs["rule"],
            "download_dir": kwargs["download_dir"],
            "files": [{"type": "txt", "path": str(tmp_path / "lecture.txt")}],
            "stt": {
                "enabled": True,
                "status": "completed",
                "txt_path": str(tmp_path / "lecture.txt"),
                "audio_path": str(tmp_path / "lecture.mp3"),
                "audio_deleted": True,
            },
        }

    monkeypatch.setattr("src.downloader.pipeline.download_lecture_media", fake_download_lecture_media)

    response = await tasks_route.start_download(
        tasks_route.DownloadTaskRequest(
            course_id=course.id,
            lecture_url="https://canvas.ssu.ac.kr/courses/42/items/1",
            lecture_title="1강",
            week_label="1주차",
        )
    )
    managed = task_manager.get(response["task_id"])
    await managed.task

    assert called["stt_enabled"] is True
    assert called["stt_model"] == "tiny"
    assert called["stt_language"] == ""
    assert called["delete_audio_after_stt"] is True
    assert managed.status == "completed"
    stt_events = event_log.list_events(event_type="stt", limit=10)
    assert stt_events[0]["action"] == "transcribe_complete"
    assert stt_events[0]["metadata"]["stt"]["audio_deleted"] is True


@pytest.mark.asyncio
async def test_start_download_passes_ai_summary_options_and_logs_completion(monkeypatch, tmp_path):
    course = _seed_course()
    Config.DOWNLOAD_RULE = "both"
    Config.STT_ENABLED = "true"
    Config.AI_ENABLED = "true"
    Config.AI_AGENT = "gemini"
    Config.GOOGLE_API_KEY = "api-key"
    Config.GEMINI_MODEL = "gemini-2.5-flash"
    Config.SUMMARY_PROMPT_TEMPLATE = "프롬프트 {text}"
    Config.SUMMARY_PROMPT_EXTRA = "핵심만"
    Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = "true"
    called = {}

    async def fake_download_lecture_media(**kwargs):
        called.update(kwargs)
        kwargs["on_stage"]("summarizing", "AI 요약 중입니다.", 98)
        return {
            "download_rule": kwargs["rule"],
            "download_dir": kwargs["download_dir"],
            "files": [{"type": "summary", "path": str(tmp_path / "lecture_summarized.txt")}],
            "stt": {"enabled": True, "status": "completed", "txt_path": str(tmp_path / "lecture.txt")},
            "summary": {
                "enabled": True,
                "status": "completed",
                "summary_path": str(tmp_path / "lecture_summarized.txt"),
                "txt_path": str(tmp_path / "lecture.txt"),
                "text_deleted": True,
            },
        }

    monkeypatch.setattr("src.downloader.pipeline.download_lecture_media", fake_download_lecture_media)

    response = await tasks_route.start_download(
        tasks_route.DownloadTaskRequest(
            course_id=course.id,
            lecture_url="https://canvas.ssu.ac.kr/courses/42/items/1",
            lecture_title="1강",
            week_label="1주차",
        )
    )
    managed = task_manager.get(response["task_id"])
    await managed.task

    assert called["ai_enabled"] is True
    assert called["ai_api_key"] == "api-key"
    assert called["ai_model"] == "gemini-2.5-flash"
    assert called["summary_prompt_template"] == "프롬프트 {text}"
    assert called["summary_prompt_extra"] == "핵심만"
    assert called["delete_text_after_summary"] is True
    summary_events = event_log.list_events(event_type="summary", limit=10)
    assert summary_events[0]["action"] == "summary_complete"
    assert summary_events[0]["metadata"]["summary"]["text_deleted"] is True


@pytest.mark.asyncio
async def test_start_download_rejects_while_playing():
    course = _seed_course()
    app_state.is_playing = True

    with pytest.raises(HTTPException) as exc:
        await tasks_route.start_download(
            tasks_route.DownloadTaskRequest(
                course_id=course.id,
                lecture_url="https://canvas.ssu.ac.kr/courses/42/items/1",
                lecture_title="1강",
                week_label="1주차",
            )
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_start_download_requires_enabled_setting():
    course = _seed_course()
    Config.DOWNLOAD_ENABLED = "false"

    with pytest.raises(HTTPException) as exc:
        await tasks_route.start_download(
            tasks_route.DownloadTaskRequest(
                course_id=course.id,
                lecture_url="https://canvas.ssu.ac.kr/courses/42/items/1",
                lecture_title="1강",
                week_label="1주차",
            )
        )

    assert exc.value.status_code == 409
    assert "영상 다운로드" in exc.value.detail


@pytest.mark.asyncio
async def test_start_download_sends_telegram_when_summary_complete(monkeypatch, tmp_path):
    """1-D: 수동 다운로드 task 완료 후 요약 파일 텔레그램 전송."""
    course = _seed_course()
    Config.AI_ENABLED = "true"
    Config.GOOGLE_API_KEY = "key"
    Config.GEMINI_MODEL = "gemini-2.5-flash"
    Config.TELEGRAM_ENABLED = "true"
    Config.TELEGRAM_BOT_TOKEN = "token"
    Config.TELEGRAM_CHAT_ID = "chat"

    summary_file = tmp_path / "lecture_summarized.txt"
    summary_file.write_text("요약 내용", encoding="utf-8")

    async def fake_download_lecture_media(**kwargs):
        return {
            "files": [],
            "stt": {"enabled": True, "status": "completed", "txt_path": str(tmp_path / "lecture.txt")},
            "summary": {
                "enabled": True,
                "status": "completed",
                "summary_path": str(summary_file),
                "txt_path": str(tmp_path / "lecture.txt"),
            },
        }

    notified = {}

    def fake_notify_summary(
        bot_token, chat_id, course_name, week_label, lecture_title, summary_text, summary_path, auto_delete_files=None
    ):
        notified["sent"] = True
        notified["course_name"] = course_name
        notified["summary_text"] = summary_text
        return True

    monkeypatch.setattr("src.downloader.pipeline.download_lecture_media", fake_download_lecture_media)
    monkeypatch.setattr("src.notifier.telegram_notifier.notify_summary_complete", fake_notify_summary)

    response = await tasks_route.start_download(
        tasks_route.DownloadTaskRequest(
            course_id=course.id,
            lecture_url="https://canvas.ssu.ac.kr/courses/42/items/1",
            lecture_title="1강",
            week_label="1주차",
        )
    )
    managed = task_manager.get(response["task_id"])
    await managed.task

    assert notified.get("sent") is True
    assert notified["course_name"] == "테스트 과목"
    assert notified["summary_text"] == "요약 내용"


@pytest.mark.asyncio
async def test_summarize_from_file_unpacks_new_path_layout(monkeypatch, tmp_path):
    """회귀 테스트: build_download_paths()가 5-tuple을 반환하도록 바뀐 뒤에도
    summarize-from-file이 정상 동작해야 한다 (이전엔 2-tuple 언패킹으로 항상 ValueError)."""
    from src.downloader.pipeline import build_download_paths

    course = _seed_course()
    Config.AI_ENABLED = "true"
    Config.GOOGLE_API_KEY = "api-key"
    Config.GEMINI_MODEL = "gemini-2.5-flash"
    monkeypatch.setattr(Config, "get_download_dir", staticmethod(lambda: str(tmp_path)))

    _base, _mp4, _mp3, txt_path, summary_path = build_download_paths(
        download_dir=str(tmp_path),
        course_name=course.long_name,
        week_label="1주차",
        lecture_title="1강",
    )
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text("강의 텍스트", encoding="utf-8")

    monkeypatch.setattr("src.summarizer.summarizer.summarize", lambda *a, **kw: summary_path)

    response = await tasks_route.start_summarize_from_file(
        tasks_route.SummarizeFromFileRequest(
            course_id=course.id,
            lecture_title="1강",
            week_label="1주차",
        )
    )
    managed = task_manager.get(response["task_id"])
    await managed.task

    assert managed.status == "completed"
    assert managed.result["summary_path"] == str(summary_path)
