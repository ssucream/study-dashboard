"""models.py 단위 테스트."""

from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week


def test_lecture_item_is_video():
    """VIDEO_LECTURE_TYPES에 해당하면 is_video=True."""
    movie = LectureItem(title="t", item_url="/a", lecture_type=LectureType.MOVIE)
    assert movie.is_video is True

    assign = LectureItem(title="t", item_url="/a", lecture_type=LectureType.ASSIGNMENT)
    assert assign.is_video is False


def test_lecture_item_needs_watch():
    """미완료 비디오 강의만 needs_watch=True."""
    lec = LectureItem(title="t", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete")
    assert lec.needs_watch is True

    lec_done = LectureItem(title="t", item_url="/a", lecture_type=LectureType.MOVIE, completion="completed")
    assert lec_done.needs_watch is False

    lec_upcoming = LectureItem(
        title="t", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete", is_upcoming=True
    )
    assert lec_upcoming.needs_watch is False

    # 출석이 인정된 상태(attendance/late/excused)면 module_item-completed가 아직 incomplete여도
    # 자동모드 재생 대상에서 제외한다 — 출석은 이미 반영됐으므로 재생은 순수 낭비.
    for status in ("attendance", "late", "excused"):
        lec_attended = LectureItem(
            title="t", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete", attendance=status
        )
        assert lec_attended.needs_watch is False, f"attendance={status} 이면 needs_watch=False"

    lec_absent = LectureItem(
        title="t", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete", attendance="absent"
    )
    assert lec_absent.needs_watch is True

    lec_no_attendance = LectureItem(
        title="t", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete", attendance="none"
    )
    assert lec_no_attendance.needs_watch is True


def test_lecture_item_needs_submission():
    """미완료 과제/퀴즈만 제출 필요로 집계한다."""
    assignment = LectureItem(title="a", item_url="/a", lecture_type=LectureType.ASSIGNMENT, completion="incomplete")
    quiz = LectureItem(title="q", item_url="/q", lecture_type=LectureType.QUIZ, completion="incomplete")
    done_quiz = LectureItem(title="done", item_url="/q2", lecture_type=LectureType.QUIZ, completion="completed")
    upcoming_assignment = LectureItem(
        title="upcoming",
        item_url="/a2",
        lecture_type=LectureType.ASSIGNMENT,
        completion="incomplete",
        is_upcoming=True,
    )
    video = LectureItem(title="v", item_url="/v", lecture_type=LectureType.MOVIE, completion="incomplete")

    assert assignment.needs_submission is True
    assert quiz.needs_submission is True
    assert done_quiz.needs_submission is False
    assert upcoming_assignment.needs_submission is False
    assert video.needs_submission is False


def test_lecture_item_full_url():
    """상대/절대 URL 처리 정확성."""
    lec_rel = LectureItem(title="t", item_url="/courses/123", lecture_type=LectureType.MOVIE)
    assert lec_rel.full_url == "https://canvas.ssu.ac.kr/courses/123"

    lec_abs = LectureItem(title="t", item_url="https://example.com/v", lecture_type=LectureType.MOVIE)
    assert lec_abs.full_url == "https://example.com/v"


def test_course_detail_counts():
    """과목 상세 비디오/제출 필요 카운트."""
    lecs = [
        LectureItem(title="v1", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete"),
        LectureItem(title="v2", item_url="/b", lecture_type=LectureType.MOVIE, completion="completed"),
        LectureItem(title="a1", item_url="/c", lecture_type=LectureType.ASSIGNMENT),
        LectureItem(title="a2", item_url="/d", lecture_type=LectureType.ASSIGNMENT, completion="completed"),
        LectureItem(title="q1", item_url="/e", lecture_type=LectureType.QUIZ),
    ]
    week = Week(title="1주차", week_number=1, lectures=lecs)
    course = Course(id="1", long_name="Test", href="/c/1", term="2026-1")
    detail = CourseDetail(course=course, course_name="Test", professors="Prof", weeks=[week])
    assert detail.total_video_count == 2
    assert detail.pending_video_count == 1
    assert detail.pending_assignment_count == 1
    assert detail.pending_quiz_count == 1


def test_week_pending_count():
    """Week의 미시청 영상/제출 필요 카운트."""
    lecs = [
        LectureItem(title="v1", item_url="/a", lecture_type=LectureType.MOVIE, completion="incomplete"),
        LectureItem(title="v2", item_url="/b", lecture_type=LectureType.READYSTREAM, completion="incomplete"),
        LectureItem(title="v3", item_url="/c", lecture_type=LectureType.MOVIE, completion="completed"),
        LectureItem(title="a1", item_url="/d", lecture_type=LectureType.ASSIGNMENT, completion="incomplete"),
        LectureItem(title="q1", item_url="/e", lecture_type=LectureType.QUIZ, completion="incomplete"),
    ]
    week = Week(title="2주차", week_number=2, lectures=lecs)
    assert week.pending_count == 2
    assert week.pending_assignment_count == 1
    assert week.pending_quiz_count == 1


def test_course_urls():
    """Course URL 프로퍼티."""
    course = Course(id="42", long_name="Test", href="/courses/42", term="2026-1")
    assert course.full_url == "https://canvas.ssu.ac.kr/courses/42"
    assert course.lectures_url == "https://canvas.ssu.ac.kr/courses/42/external_tools/71"


def test_all_video_lecture_types():
    """모든 비디오 타입이 is_video=True."""
    for lt in (
        LectureType.MOVIE,
        LectureType.READYSTREAM,
        LectureType.SCREENLECTURE,
        LectureType.EVERLEC,
        LectureType.MP4,
    ):
        lec = LectureItem(title="t", item_url="/a", lecture_type=lt)
        assert lec.is_video is True, f"{lt} should be video"

    for lt in (LectureType.ASSIGNMENT, LectureType.QUIZ, LectureType.DISCUSSION, LectureType.FILE, LectureType.OTHER):
        lec = LectureItem(title="t", item_url="/a", lecture_type=lt)
        assert lec.is_video is False, f"{lt} should not be video"
