"""
마감 임박 알림 모듈.

비디오가 아닌 강의 항목(퀴즈, 과제 등)의 마감이 임박할 때
텔레그램으로 알림을 전송한다.
"""

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime

from src.config import KST, get_data_path
from src.scraper.models import VIDEO_LECTURE_TYPES, Course, CourseDetail, LectureItem, LectureType

_DEADLINE_FILE = get_data_path("deadline_notified.json")

# 대시보드 표시는 전송 시점 설정과 무관하게 항상 7일 이내 전체를 보여준다.
_DISPLAY_WINDOW_HOURS = 168

_TYPE_LABELS = {
    LectureType.QUIZ: "퀴즈",
    LectureType.ASSIGNMENT: "과제",
    LectureType.DISCUSSION: "토론",
    LectureType.WIKI_PAGE: "위키",
    LectureType.FILE: "파일",
    LectureType.ZOOM: "Zoom",
    LectureType.OTHER: "기타",
}


@dataclass
class DeadlineItem:
    """마감 임박 항목 (표시용)."""

    course: Course
    lecture: LectureItem
    type_label: str
    remaining_hours: float


@dataclass
class DeadlineNotification:
    """전송 대상 마감 알림 (항목 + 넘어선 알림 시점)."""

    item: DeadlineItem
    threshold: int
    dedup_key: str


def _parse_lms_date(date_str: str, now: datetime | None = None) -> datetime | None:
    """LMS 날짜 문자열을 파싱한다. (예: '3월 19일 오후 11:59')

    연도 전환기(12월→1월, 1월→12월) 보정:
    - 현재 11~12월인데 파싱 월이 1~2월이면 다음 해
    - 현재 1~2월인데 파싱 월이 11~12월이면 전년도
    """
    if not date_str:
        return None
    match = re.match(r"(\d+)월\s*(\d+)일(?:\s*(오전|오후)\s*(\d+):(\d+))?", date_str.strip())
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    ampm = match.group(3)
    hour = int(match.group(4)) if match.group(4) else 23
    minute = int(match.group(5)) if match.group(5) else 59

    if ampm == "오후" and hour != 12:
        hour += 12
    elif ampm == "오전" and hour == 12:
        hour = 0

    if now is None:
        now = datetime.now(KST)
    year = now.year

    # 연도 전환기 보정
    if now.month >= 11 and month <= 2:
        year += 1
    elif now.month <= 2 and month >= 11:
        year -= 1

    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST)
    except ValueError:
        return None


def _make_dedup_key(course: Course, lecture: LectureItem, threshold: int) -> str:
    """과목 ID + 강의 제목 해시 기반의 안정적인 dedup 키를 생성한다."""
    stable_id = hashlib.sha256(f"{course.id}:{lecture.title}".encode()).hexdigest()[:16]
    return f"{stable_id}:{threshold}"


def _load_notified() -> set[str]:
    try:
        if _DEADLINE_FILE.exists():
            return set(json.loads(_DEADLINE_FILE.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        print("  [경고] deadline_notified.json 파싱 실패 — 초기화합니다.", file=sys.stderr)
    except Exception:
        pass
    return set()


def _save_notified(notified: set[str]) -> None:
    try:
        _DEADLINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DEADLINE_FILE.write_text(json.dumps(sorted(notified)), encoding="utf-8")
    except Exception as e:
        print(f"  [경고] deadline_notified.json 저장 실패: {e}", file=sys.stderr)


def _iter_pending_deadlines(
    courses: list[Course],
    details: list[CourseDetail | None],
    now: datetime,
):
    """미완료·비디오 외 강의 중 마감이 남은 항목을 순회한다.

    Yields:
        (course, lecture, type_label, remaining_hours)
    """
    for course, detail in zip(courses, details, strict=False):
        if detail is None:
            continue
        for week in detail.weeks:
            for lec in week.lectures:
                if lec.lecture_type in VIDEO_LECTURE_TYPES:
                    continue
                # 완료 판별: completion 또는 attendance 둘 중 하나라도 완료면 건너뜀
                if lec.completion == "completed":
                    continue
                if lec.attendance in ("attendance", "late", "excused"):
                    continue
                if lec.is_upcoming:
                    continue
                if not lec.end_date:
                    continue

                deadline = _parse_lms_date(lec.end_date, now=now)
                if deadline is None:
                    continue

                remaining_hours = (deadline - now).total_seconds() / 3600
                if remaining_hours <= 0:
                    continue

                type_label = _TYPE_LABELS.get(lec.lecture_type, lec.lecture_type.value)
                yield course, lec, type_label, remaining_hours


def find_approaching_deadlines(
    courses: list[Course],
    details: list[CourseDetail | None],
    now: datetime | None = None,
    within_hours: int = _DISPLAY_WINDOW_HOURS,
) -> list[DeadlineItem]:
    """표시용: within_hours 이내에 마감되는 강의를 강의당 1건 반환한다.

    알림 전송 여부/시점 설정과 무관하며, 대시보드 목록 표시에 쓰인다.
    """
    if now is None:
        now = datetime.now(KST)

    return [
        DeadlineItem(course=course, lecture=lec, type_label=type_label, remaining_hours=remaining_hours)
        for course, lec, type_label, remaining_hours in _iter_pending_deadlines(courses, details, now)
        if remaining_hours <= within_hours
    ]


def find_deadline_notifications(
    courses: list[Course],
    details: list[CourseDetail | None],
    thresholds: list[int],
    notified: set[str] | None = None,
    now: datetime | None = None,
) -> list[DeadlineNotification]:
    """전송용: 아직 알리지 않은 (강의, 넘어선 알림 시점) 쌍을 반환한다."""
    if now is None:
        now = datetime.now(KST)
    if notified is None:
        notified = set()

    result: list[DeadlineNotification] = []
    for course, lec, type_label, remaining_hours in _iter_pending_deadlines(courses, details, now):
        for threshold in thresholds:
            if remaining_hours > threshold:
                continue
            key = _make_dedup_key(course, lec, threshold)
            if key in notified:
                continue
            result.append(
                DeadlineNotification(
                    item=DeadlineItem(
                        course=course,
                        lecture=lec,
                        type_label=type_label,
                        remaining_hours=remaining_hours,
                    ),
                    threshold=threshold,
                    dedup_key=key,
                )
            )
    return result


def check_and_notify_deadlines(
    courses: list[Course],
    details: list[CourseDetail | None],
    token: str = "",
    chat_id: str = "",
) -> int:
    """마감 임박 항목을 확인하고 텔레그램으로 알림을 전송한다.

    전송 시점은 Config.get_deadline_thresholds()(사용자 설정)를 따른다.
    token/chat_id를 생략하면 Config 값을 사용한다.

    Returns:
        전송된 알림 수
    """
    from src.config import Config

    if not Config.should_notify("deadline"):
        return 0

    token = token or Config.TELEGRAM_BOT_TOKEN or ""
    chat_id = chat_id or Config.TELEGRAM_CHAT_ID or ""
    if not token or not chat_id:
        return 0

    from src.notifier.telegram_notifier import notify_deadline_warning

    notified = _load_notified()
    pending = find_deadline_notifications(
        courses, details, Config.get_deadline_thresholds(), notified=notified
    )
    if not pending:
        return 0

    sent_count = 0
    for note in pending:
        item = note.item
        ok = notify_deadline_warning(
            bot_token=token,
            chat_id=chat_id,
            course_name=item.course.long_name,
            week_label=item.lecture.week_label,
            lecture_title=item.lecture.title,
            type_label=item.type_label,
            end_date=item.lecture.end_date or "",
            remaining_hours=item.remaining_hours,
        )
        if ok:
            notified.add(note.dedup_key)
            sent_count += 1

    if sent_count > 0:
        _save_notified(notified)

    return sent_count
