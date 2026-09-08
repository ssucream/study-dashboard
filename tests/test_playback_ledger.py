"""재생 재시도 억제 원장 테스트."""

import pytest

from src import playback_ledger


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    yield


def _fail(**kwargs):
    return playback_ledger.record_attempt(
        "1",
        "https://canvas.ssu.ac.kr/courses/1/items/1",
        course_name="성서읽기",
        lecture_title="1주차 Intro",
        week_label="1주차",
        result="failed",
        error="출석 미반영",
        **kwargs,
    )


def test_three_failures_suppress_and_report_transition_once():
    """max_attempts=3이면 3회째에 억제되고, suppressed_now는 그때만 True다."""
    assert _fail(max_attempts=3) == (1, False)
    assert _fail(max_attempts=3) == (2, False)
    assert _fail(max_attempts=3) == (3, True)
    # 이후 실패는 이미 억제된 상태 — 중복 알림이 나가지 않아야 한다
    count, suppressed_now = _fail(max_attempts=3)
    assert count == 4
    assert suppressed_now is False
    assert playback_ledger.is_suppressed("1", "https://canvas.ssu.ac.kr/courses/1/items/1") is True


def test_verified_clears_history():
    """성공하면 행을 삭제해 이전 실패 이력을 초기화한다."""
    _fail(max_attempts=3)
    _fail(max_attempts=3)

    playback_ledger.record_attempt("1", "https://canvas.ssu.ac.kr/courses/1/items/1", result="verified")

    assert playback_ledger.is_suppressed("1", "https://canvas.ssu.ac.kr/courses/1/items/1") is False
    assert playback_ledger.list_suppressed() == []
    # 이력이 초기화됐으므로 다음 실패는 다시 1회차부터
    assert _fail(max_attempts=3) == (1, False)


def test_transient_error_does_not_count_toward_suppression():
    """일시적 재생 오류는 관측만 하고 억제 카운트를 올리지 않는다."""
    for _ in range(5):
        count, suppressed_now = playback_ledger.record_attempt(
            "1",
            "https://canvas.ssu.ac.kr/courses/1/items/1",
            result=playback_ledger.RESULT_ERROR,
            error="브라우저 크래시",
            max_attempts=3,
        )
        assert (count, suppressed_now) == (0, False)
    assert playback_ledger.is_suppressed("1", "https://canvas.ssu.ac.kr/courses/1/items/1") is False

    # 마지막 시도 정보는 남아 있어야 한다 (관측 목적)
    assert playback_ledger.suppressed_urls() == set()
    _fail(max_attempts=3)  # 진짜 미반영은 정상적으로 1회차부터 카운트
    assert _fail(max_attempts=3) == (2, False)


def test_max_attempts_zero_never_suppresses():
    """0은 무제한 — 몇 번을 실패해도 억제하지 않는다 (롤백 스위치)."""
    for _ in range(10):
        _, suppressed_now = _fail(max_attempts=0)
        assert suppressed_now is False
    assert playback_ledger.is_suppressed("1", "https://canvas.ssu.ac.kr/courses/1/items/1") is False


def test_suppressed_urls_and_list_suppressed():
    _fail(max_attempts=1)
    playback_ledger.record_attempt("2", "url-b", result="failed", max_attempts=1)
    playback_ledger.record_attempt("2", "url-c", result="unverified", max_attempts=5)

    urls = playback_ledger.suppressed_urls()
    assert urls == {"https://canvas.ssu.ac.kr/courses/1/items/1", "url-b"}
    assert playback_ledger.suppressed_urls(course_id="2") == {"url-b"}

    rows = playback_ledger.list_suppressed()
    assert len(rows) == 2
    row = next(r for r in rows if r["course_id"] == "1")
    assert row["lecture_title"] == "1주차 Intro"
    assert row["last_result"] == "failed"
    assert row["last_error"] == "출석 미반영"
    assert row["suppressed_at"]
    assert row["notified"] == 0


def test_reset_removes_rows():
    _fail(max_attempts=1)
    playback_ledger.record_attempt("2", "url-b", result="failed", max_attempts=1)

    assert playback_ledger.reset("1", "https://canvas.ssu.ac.kr/courses/1/items/1") == 1
    assert playback_ledger.suppressed_urls() == {"url-b"}

    assert playback_ledger.reset() == 1
    assert playback_ledger.suppressed_urls() == set()


def test_mark_notified_sets_flag():
    _fail(max_attempts=1)
    playback_ledger.mark_notified("1", "https://canvas.ssu.ac.kr/courses/1/items/1")
    assert playback_ledger.list_suppressed()[0]["notified"] == 1


def test_get_auto_max_retry_normalizes():
    from src.config import Config

    Config.AUTO_MAX_RETRY_PER_LECTURE = "5"
    assert Config.get_auto_max_retry() == 5
    Config.AUTO_MAX_RETRY_PER_LECTURE = "0"
    assert Config.get_auto_max_retry() == 0
    Config.AUTO_MAX_RETRY_PER_LECTURE = "-2"
    assert Config.get_auto_max_retry() == 3
    Config.AUTO_MAX_RETRY_PER_LECTURE = "abc"
    assert Config.get_auto_max_retry() == 3
    Config.AUTO_MAX_RETRY_PER_LECTURE = ""
    assert Config.get_auto_max_retry() == 3
    Config.AUTO_MAX_RETRY_PER_LECTURE = "3"
