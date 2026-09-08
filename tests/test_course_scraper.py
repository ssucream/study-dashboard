"""CourseScraper의 순수 파싱 헬퍼 테스트.

class 문자열은 실제 canvas.ssu.ac.kr Learning X DOM에서 캡처한 값이다.
"""

from src.scraper.course_scraper import _parse_attendance_class, _parse_completion_class


class TestParseAttendanceClass:
    def test_attended(self):
        # <span class="xnmb-module_item-meta_data-attendance_status attendance">출석</span>
        assert _parse_attendance_class("xnmb-module_item-meta_data-attendance_status attendance") == "attendance"

    def test_not_attended_is_none_not_attendance(self):
        # <span class="xnmb-module_item-meta_data-attendance_status none">-</span>
        # 기저 클래스명에 "attendance"가 들어 있어도 부분 문자열 오판이 없어야 한다.
        assert _parse_attendance_class("xnmb-module_item-meta_data-attendance_status none") == "none"

    def test_late(self):
        assert _parse_attendance_class("xnmb-module_item-meta_data-attendance_status late") == "late"

    def test_absent(self):
        assert _parse_attendance_class("xnmb-module_item-meta_data-attendance_status absent") == "absent"

    def test_excused(self):
        assert _parse_attendance_class("xnmb-module_item-meta_data-attendance_status excused") == "excused"

    def test_empty(self):
        assert _parse_attendance_class("") == "none"


class TestParseCompletionClass:
    def test_completed(self):
        # <span class="xnmb-module_item-completed completed">완료...</span>
        assert _parse_completion_class("xnmb-module_item-completed completed") == "completed"

    def test_incomplete_not_misread_as_completed(self):
        # <span class="xnmb-module_item-completed incomplete">-</span>
        # "completed"가 기저 클래스명의 부분 문자열이지만 토큰이 아니므로 incomplete여야 한다.
        assert _parse_completion_class("xnmb-module_item-completed incomplete") == "incomplete"

    def test_empty(self):
        assert _parse_completion_class("") == "incomplete"
