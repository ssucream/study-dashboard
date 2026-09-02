"""마감 알림 로직 테스트 — 표시/전송 분리, 시점 설정 파싱, 카테고리 게이트."""

from datetime import datetime

import pytest

from src.config import KST, Config, parse_deadline_thresholds
from src.notifier import deadline_checker as dc
from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=KST)


def _detail(*lectures: LectureItem) -> CourseDetail:
    course = Course(id="1", long_name="과목", href="/courses/1", term="2026-2")
    return CourseDetail(course=course, course_name="과목", professors="", weeks=[Week("1주차", 1, list(lectures))])


def _quiz(title: str, end_date: str | None, **kw) -> LectureItem:
    return LectureItem(title=title, item_url=f"/q/{title}", lecture_type=LectureType.QUIZ, end_date=end_date, **kw)


# ── parse_deadline_thresholds ────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("168,72,24,12", [168, 72, 24, 12]),
        (" 24 , 12 ,foo,-5,0", [24, 12]),
        ("12,12,24", [24, 12]),
        ("", [168, 72, 24, 12]),
        ("garbage", [168, 72, 24, 12]),
        (None, [168, 72, 24, 12]),
    ],
)
def test_parse_deadline_thresholds(raw, expected):
    assert parse_deadline_thresholds(raw) == expected


# ── find_approaching_deadlines (표시용) ──────────────────────────────


def test_find_approaching_returns_one_item_per_lecture_within_window():
    detail = _detail(
        _quiz("임박", "3월 15일 오후 6:00"),  # ~6h
        _quiz("사흘", "3월 18일 오후 11:59"),  # ~84h
        _quiz("먼미래", "3월 30일 오후 11:59"),  # >168h → 제외
        _quiz("완료", "3월 16일 오후 1:00", completion="completed"),  # 제외
        _quiz("예정", "3월 16일 오후 1:00", is_upcoming=True),  # 제외
    )
    items = dc.find_approaching_deadlines([detail.course], [detail], now=_NOW)

    assert sorted(i.lecture.title for i in items) == ["사흘", "임박"]
    # 강의당 정확히 1건
    assert len(items) == len({i.lecture.title for i in items})


def test_find_approaching_ignores_video_lectures():
    video = LectureItem(title="영상", item_url="/v", lecture_type=LectureType.MOVIE, end_date="3월 16일 오후 1:00")
    detail = _detail(video)
    assert dc.find_approaching_deadlines([detail.course], [detail], now=_NOW) == []


# ── find_deadline_notifications (전송용) ─────────────────────────────


def test_find_deadline_notifications_only_crossed_thresholds():
    detail = _detail(_quiz("사흘", "3월 18일 오후 11:59"))  # ~84h 남음
    notes = dc.find_deadline_notifications([detail.course], [detail], thresholds=[168, 72, 24], now=_NOW)
    # 84h <= 168 만 충족 (84 > 72, 84 > 24)
    assert [n.threshold for n in notes] == [168]


def test_find_deadline_notifications_skips_already_notified():
    detail = _detail(_quiz("사흘", "3월 18일 오후 11:59"))
    first = dc.find_deadline_notifications([detail.course], [detail], thresholds=[168], now=_NOW)
    notified = {first[0].dedup_key}
    again = dc.find_deadline_notifications([detail.course], [detail], thresholds=[168], notified=notified, now=_NOW)
    assert again == []


# ── Config.should_notify ────────────────────────────────────────────


@pytest.fixture
def telegram_ready(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_ENABLED", "true")
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "chat")
    for attr in (
        "TELEGRAM_NOTIFY_PLAYBACK",
        "TELEGRAM_NOTIFY_SUMMARY",
        "TELEGRAM_NOTIFY_ERROR",
        "TELEGRAM_NOTIFY_DEADLINE",
    ):
        monkeypatch.setattr(Config, attr, "true")


def test_should_notify_false_without_credentials(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_ENABLED", "false")
    assert Config.should_notify("playback") is False


def test_should_notify_true_when_ready(telegram_ready):
    assert Config.should_notify("playback") is True
    assert Config.should_notify("deadline") is True


def test_should_notify_respects_category_toggle(telegram_ready, monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_NOTIFY_DEADLINE", "false")
    assert Config.should_notify("deadline") is False
    assert Config.should_notify("playback") is True


def test_check_and_notify_deadlines_gated_by_toggle(telegram_ready, monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_NOTIFY_DEADLINE", "false")
    sent = {"n": 0}
    monkeypatch.setattr(
        "src.notifier.telegram_notifier.notify_deadline_warning",
        lambda **kw: sent.__setitem__("n", sent["n"] + 1) or True,
    )
    detail = _detail(_quiz("사흘", "3월 18일 오후 11:59"))
    assert dc.check_and_notify_deadlines([detail.course], [detail]) == 0
    assert sent["n"] == 0
