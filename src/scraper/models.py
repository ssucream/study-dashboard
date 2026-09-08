from dataclasses import dataclass, field
from enum import Enum

_BASE_URL = "https://canvas.ssu.ac.kr"


class LectureType(Enum):
    MOVIE = "movie"
    READYSTREAM = "readystream"
    SCREENLECTURE = "screenlecture"
    EVERLEC = "everlec"
    ZOOM = "zoom"
    MP4 = "mp4"
    ASSIGNMENT = "assignment"
    WIKI_PAGE = "wiki_page"
    QUIZ = "quiz"
    DISCUSSION = "discussion"
    FILE = "file"
    OTHER = "other"


VIDEO_LECTURE_TYPES = {
    LectureType.MOVIE,
    LectureType.READYSTREAM,
    LectureType.SCREENLECTURE,
    LectureType.EVERLEC,
    LectureType.MP4,
}

SUBMISSION_REQUIRED_TYPES = {
    LectureType.ASSIGNMENT,
    LectureType.QUIZ,
}

# 스크래핑 attendance 값 중 출석 인정으로 볼 값 (CLAUDE.md "출석 상태" 표: attendance/late/excused, absent 제외).
# 재생 후 재검증 성공 판정(src/player/background_player.py)과 자동모드 재생 대상 판정(needs_watch)이 공유한다.
ATTENDED_STATUSES = frozenset({"attendance", "late", "excused"})


@dataclass
class Course:
    id: str
    long_name: str
    href: str
    term: str
    is_favorited: bool = False

    @property
    def full_url(self) -> str:
        return f"{_BASE_URL}{self.href}"

    @property
    def lectures_url(self) -> str:
        return f"{_BASE_URL}/courses/{self.id}/external_tools/71"


@dataclass
class LectureItem:
    title: str
    item_url: str
    lecture_type: LectureType
    week_label: str = ""
    lesson_label: str = ""
    duration: str | None = None
    attendance: str = "none"
    completion: str = "incomplete"
    content_type_label: str = ""
    is_upcoming: bool = False
    start_date: str | None = None
    end_date: str | None = None

    @property
    def is_video(self) -> bool:
        return self.lecture_type in VIDEO_LECTURE_TYPES

    @property
    def full_url(self) -> str:
        if self.item_url.startswith("http"):
            return self.item_url
        return f"{_BASE_URL}{self.item_url}"

    @property
    def needs_watch(self) -> bool:
        return (
            self.is_video
            and not self.is_upcoming
            and self.completion != "completed"
            and self.attendance not in ATTENDED_STATUSES
        )

    @property
    def needs_submission(self) -> bool:
        return (
            self.lecture_type in SUBMISSION_REQUIRED_TYPES and self.completion != "completed" and not self.is_upcoming
        )


@dataclass
class Week:
    title: str
    week_number: int
    lectures: list[LectureItem] = field(default_factory=list)

    @property
    def video_lectures(self) -> list[LectureItem]:
        return [lec for lec in self.lectures if lec.is_video]

    @property
    def pending_count(self) -> int:
        return sum(1 for lec in self.lectures if lec.needs_watch)

    @property
    def pending_assignment_count(self) -> int:
        return sum(1 for lec in self.lectures if lec.lecture_type == LectureType.ASSIGNMENT and lec.needs_submission)

    @property
    def pending_quiz_count(self) -> int:
        return sum(1 for lec in self.lectures if lec.lecture_type == LectureType.QUIZ and lec.needs_submission)


@dataclass
class CourseDetail:
    course: Course
    course_name: str
    professors: str
    weeks: list[Week] = field(default_factory=list)

    @property
    def all_video_lectures(self) -> list[LectureItem]:
        result = []
        for week in self.weeks:
            result.extend(week.video_lectures)
        return result

    @property
    def total_video_count(self) -> int:
        return len(self.all_video_lectures)

    @property
    def pending_video_count(self) -> int:
        return sum(1 for lec in self.all_video_lectures if lec.needs_watch)

    @property
    def pending_assignment_count(self) -> int:
        return sum(week.pending_assignment_count for week in self.weeks)

    @property
    def pending_quiz_count(self) -> int:
        return sum(week.pending_quiz_count for week in self.weeks)
