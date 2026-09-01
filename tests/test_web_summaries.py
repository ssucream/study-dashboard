"""웹 요약 조회/강의 상세 연동 테스트."""

import pytest
from backend.api import summary_store
from backend.api.routes import courses as courses_route
from backend.api.routes import summaries as summaries_route
from backend.api.state import PlaybackProgress, app_state

from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week


class _FakeScraper:
    pass


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


def _seed_course(completion: str = "completed") -> tuple[Course, LectureItem]:
    course = Course(id="42", long_name="테스트 과목", href="/courses/42", term="2026-1")
    lecture = LectureItem(
        title="1강",
        item_url="/courses/42/lecture_attendance/items/view/1",
        lecture_type=LectureType.MOVIE,
        week_label="1주차",
        completion=completion,
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
def reset_state():
    _reset_app_state()
    yield
    _reset_app_state()


@pytest.mark.asyncio
async def test_course_detail_exposes_generated_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(summary_store, "summaries_dir", lambda: tmp_path / "summaries")
    course, lecture = _seed_course()
    summary_path = summary_store._canonical_summary_path(
        course.term, course.long_name, lecture.week_label, lecture.title
    )
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("# 1강 요약\n\n- 핵심 내용", encoding="utf-8")

    result = await courses_route.get_course_detail(course.id)
    lecture_payload = result["weeks"][0]["lectures"][0]

    assert lecture_payload["has_summary"] is True
    assert lecture_payload["summary"]["available"] is True
    assert lecture_payload["summary_id"]


@pytest.mark.asyncio
async def test_courses_payload_exposes_submission_counts():
    course, lecture = _seed_course(completion="incomplete")
    assignment = LectureItem(
        title="과제",
        item_url="/courses/42/assignments/1",
        lecture_type=LectureType.ASSIGNMENT,
        completion="incomplete",
    )
    quiz = LectureItem(
        title="퀴즈",
        item_url="/courses/42/quizzes/1",
        lecture_type=LectureType.QUIZ,
        completion="incomplete",
    )
    done_quiz = LectureItem(
        title="완료 퀴즈",
        item_url="/courses/42/quizzes/2",
        lecture_type=LectureType.QUIZ,
        completion="completed",
    )
    app_state.details = [
        CourseDetail(
            course=course,
            course_name=course.long_name,
            professors="교수",
            weeks=[Week(title="1주차", week_number=1, lectures=[lecture, assignment, quiz, done_quiz])],
        )
    ]

    courses = await courses_route.get_courses()
    stats = await courses_route.get_stats()

    assert courses[0]["pending_videos"] == 1
    assert courses[0]["pending_assignments"] == 1
    assert courses[0]["pending_quizzes"] == 1
    assert stats["pending_videos"] == 1
    assert stats["pending_assignments"] == 1
    assert stats["pending_quizzes"] == 1


@pytest.mark.asyncio
async def test_get_summary_reads_markdown_safely(monkeypatch, tmp_path):
    monkeypatch.setattr(summary_store, "summaries_dir", lambda: tmp_path / "summaries")
    course, lecture = _seed_course()
    summary_path = summary_store._canonical_summary_path(
        course.term, course.long_name, lecture.week_label, lecture.title
    )
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text("# 1강 요약\n\n<script>alert(1)</script>", encoding="utf-8")
    summary = summary_store.summary_for_lecture(course.term, course.long_name, lecture.week_label, lecture.title)

    result = await summaries_route.get_summary(summary["id"])

    assert result["title"] == "1강"
    assert result["format"] == "markdown"
    assert "<script>alert(1)</script>" in result["content"]


def _write_pipeline_summary(monkeypatch, tmp_path, course, lecture, text="1강 요약 본문"):
    """다운로드 파이프라인이 쓰는 위치(downloads/summarized/...)에 요약본을 생성한다."""
    from src.config import Config

    monkeypatch.setattr(Config, "get_download_dir", classmethod(lambda cls: str(tmp_path)))
    monkeypatch.setattr(summary_store, "summaries_dir", lambda: tmp_path / "summaries")
    path = summary_store._pipeline_summary_path(course.long_name, lecture.week_label, lecture.title)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_course_detail_detects_pipeline_generated_summary(monkeypatch, tmp_path):
    """자동 재생 파이프라인이 생성한 요약본이 강의 상세의 has_summary에 반영돼야 한다."""
    course, lecture = _seed_course()
    _write_pipeline_summary(monkeypatch, tmp_path, course, lecture)

    result = await courses_route.get_course_detail(course.id)
    lecture_payload = result["weeks"][0]["lectures"][0]

    assert lecture_payload["has_summary"] is True
    assert lecture_payload["summary"]["available"] is True
    assert lecture_payload["summary_id"]


@pytest.mark.asyncio
async def test_summaries_list_includes_pipeline_generated_summary(monkeypatch, tmp_path):
    """학습 결과 목록이 downloads/summarized 아래 요약본도 나열해야 한다."""
    course, lecture = _seed_course()
    _write_pipeline_summary(monkeypatch, tmp_path, course, lecture)

    payload = await summaries_route.get_summaries_list()

    assert len(payload["summaries"]) == 1
    item = payload["summaries"][0]
    assert item["course"] == "테스트 과목"
    assert item["week"] == "1주차"
    assert item["title"] == "1강"
    assert item["format"] == "text"

    # 목록의 id로 실제 내용을 읽을 수 있어야 한다
    detail = await summaries_route.get_summary(item["id"])
    assert detail["content"] == "1강 요약 본문"


@pytest.mark.asyncio
async def test_summaries_list_prefers_canonical_over_pipeline_duplicate(monkeypatch, tmp_path):
    course, lecture = _seed_course()
    _write_pipeline_summary(monkeypatch, tmp_path, course, lecture, text="pipeline 본문")
    canonical = summary_store._canonical_summary_path(course.term, course.long_name, lecture.week_label, lecture.title)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("# canonical", encoding="utf-8")

    payload = await summaries_route.get_summaries_list()

    assert len(payload["summaries"]) == 1
    assert payload["summaries"][0]["format"] == "markdown"
