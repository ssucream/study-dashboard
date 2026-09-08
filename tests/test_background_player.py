"""background_player의 출석 반영 검증 로직 테스트."""

import json

import pytest

from src.player import background_player
from src.player.background_player import (
    _ATTENDANCE_MIN_RATIO,
    PlaybackState,
    _confirm_lms_attendance,
    _extract_watched_seconds,
    _verify_via_rescrape,
)


@pytest.fixture(autouse=True)
def _no_verify_backoff(monkeypatch):
    """재스크래핑 검증 backoff를 0으로 만들어 테스트를 즉시 끝낸다."""
    monkeypatch.setattr(background_player, "_VERIFY_BACKOFF", (0, 0, 0))


class _FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body


class _FakeRequestCtx:
    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[str] = []

    async def get(self, url: str, **kwargs) -> _FakeResponse:
        self.calls.append(url)
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


class _FakePage:
    def __init__(self, responses: list[_FakeResponse]):
        self.request = _FakeRequestCtx(responses)


def _log(*_a, **_k):
    pass


# ── _extract_watched_seconds ─────────────────────────────────────


def test_extract_watched_seconds_falls_back_to_viewer_url_endat():
    data = {"viewer_url": "https://commons.ssu.ac.kr/em/x?startat=0.00&endat=1780.50&TargetUrl=y"}
    assert _extract_watched_seconds(data) == (pytest.approx(1780.5), "endat")


def test_extract_watched_seconds_from_explicit_field():
    data = {"item_content_data": {"duration": 1800}, "attendance": {"cumulative_second": 1700}}
    assert _extract_watched_seconds(data) == (1700.0, "field")


def test_extract_watched_seconds_prefers_explicit_field_over_endat():
    """endat은 우리가 써넣은 자체 보고값일 수 있으므로 명시적 누적 필드가 우선한다."""
    data = {
        "viewer_url": "x?endat=1500.00",
        "attendance": {"cumulative_second": 900},
        "current_time": 10,  # 모호한 필드 — 무시돼야 함
    }
    assert _extract_watched_seconds(data) == (900.0, "field")


def test_extract_watched_seconds_ignores_ambiguous_fields():
    data = {"viewer_url": "x?endat=1500.00", "current_time": 10}
    assert _extract_watched_seconds(data) == (1500.0, "endat")


def test_extract_watched_seconds_returns_none_when_unknown_shape():
    assert _extract_watched_seconds({"foo": "bar", "current_time": 3}) is None
    assert _extract_watched_seconds({}) is None


# ── _confirm_lms_attendance ──────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_marks_confirmed_when_lms_progress_high():
    state = PlaybackState(duration=1000, ended=True)
    body = json.dumps({"viewer_url": "x?endat=990.00"})
    page = _FakePage([_FakeResponse(200, body)])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.lms_progress_ratio == pytest.approx(0.99)
    assert state.progress_reported is True
    assert state.error is None


@pytest.mark.asyncio
async def test_confirm_sets_error_when_both_signals_negative():
    """A안(progress_reported=False) + B안(LMS 진도 낮음) 둘 다 음성이면 실패 처리."""
    state = PlaybackState(duration=1000, ended=True, progress_reported=False)
    body = json.dumps({"viewer_url": "x?endat=120.00"})
    page = _FakePage([_FakeResponse(200, body)])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.lms_progress_ratio == pytest.approx(0.12)
    assert state.progress_reported is False
    assert state.error is not None
    assert "출석" in state.error


@pytest.mark.asyncio
async def test_confirm_keeps_completed_when_report_ok_but_lms_progress_lagging():
    """진도 보고는 수락(progress_reported=True)됐는데 재조회 진도만 낮으면 반영 지연으로 보고 완료 유지."""
    state = PlaybackState(duration=1000, ended=True, progress_reported=True)
    body = json.dumps({"viewer_url": "x?endat=120.00"})
    page = _FakePage([_FakeResponse(200, body)])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.lms_progress_ratio == pytest.approx(0.12)
    assert state.progress_reported is True
    assert state.error is None


@pytest.mark.asyncio
async def test_confirm_records_watched_source_for_endat_fallback():
    """endat 폴백을 썼다면 출처를 state에 남겨 이벤트 metadata로 측정할 수 있게 한다."""
    state = PlaybackState(duration=1000, ended=True, progress_reported=True)
    page = _FakePage([_FakeResponse(200, json.dumps({"viewer_url": "x?endat=990.00"}))])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.watched_source == "endat"


@pytest.mark.asyncio
async def test_confirm_records_watched_source_for_explicit_field():
    state = PlaybackState(duration=1000, ended=True)
    body = json.dumps({"attendance": {"cumulative_second": 950}, "viewer_url": "x?endat=990.00"})
    page = _FakePage([_FakeResponse(200, body)])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.watched_source == "field"
    assert state.lms_progress_ratio == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_confirm_noop_when_api_url_missing():
    state = PlaybackState(duration=1000, ended=True, progress_reported=True)
    page = _FakePage([_FakeResponse(200, "{}")])

    await _confirm_lms_attendance(page, "", state, _log)

    assert state.error is None
    assert state.progress_reported is True  # A안 결과 유지
    assert page.request.calls == []


@pytest.mark.asyncio
async def test_confirm_noop_when_api_unreachable():
    state = PlaybackState(duration=1000, ended=True, progress_reported=True)
    page = _FakePage([_FakeResponse(500, ""), _FakeResponse(500, ""), _FakeResponse(500, "")])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.error is None
    assert state.progress_reported is True  # 조회 불가 → A안에 위임


@pytest.mark.asyncio
async def test_confirm_noop_when_progress_field_unparseable():
    state = PlaybackState(duration=1000, ended=True, progress_reported=False)
    page = _FakePage([_FakeResponse(200, json.dumps({"unknown": 1}))])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log)

    assert state.error is None
    assert state.lms_progress_ratio is None


def test_attendance_min_ratio_is_reasonable():
    assert 0.5 < _ATTENDANCE_MIN_RATIO <= 1.0


@pytest.mark.asyncio
async def test_confirm_does_not_set_error_when_set_error_false():
    """재스크래핑 검증이 최종 판정을 맡으면 _confirm_lms_attendance는 관측만 한다."""
    state = PlaybackState(duration=1000, ended=True, progress_reported=False)
    body = json.dumps({"viewer_url": "x?endat=120.00"})
    page = _FakePage([_FakeResponse(200, body)])

    await _confirm_lms_attendance(page, "https://.../attendance_items/1", state, _log, set_error=False)

    assert state.lms_progress_ratio == pytest.approx(0.12)
    assert state.error is None  # 판정은 _verify_via_rescrape가 한다


# ── _verify_via_rescrape ─────────────────────────────────────────


def _verify_returning(*results):
    """호출할 때마다 results를 순서대로 반환/raise하는 verify_fn을 만든다."""
    queue = list(results)

    async def _fn():
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return _fn


@pytest.mark.asyncio
async def test_verify_marks_verified_when_completion_completed():
    state = PlaybackState(duration=1000, ended=True)

    await _verify_via_rescrape(_verify_returning(("completed", "none")), state, _log)

    assert state.verified is True
    assert state.lms_completion == "completed"
    assert state.error is None
    assert state.verify_attempts == 1


@pytest.mark.asyncio
async def test_verify_marks_verified_when_attendance_recorded():
    """completion이 incomplete여도 출석(attendance)이 잡히면 성공으로 본다."""
    state = PlaybackState(duration=1000, ended=True)

    await _verify_via_rescrape(_verify_returning(("incomplete", "attendance")), state, _log)

    assert state.verified is True
    assert state.lms_attendance == "attendance"
    assert state.error is None


@pytest.mark.asyncio
async def test_verify_marks_unverified_after_three_negative_results():
    state = PlaybackState(duration=1000, ended=True)

    await _verify_via_rescrape(_verify_returning(("incomplete", "absent")), state, _log)

    assert state.verified is False
    assert state.error is not None
    assert "출석" in state.error
    assert state.verify_attempts == 3


@pytest.mark.asyncio
async def test_verify_leaves_none_when_rescrape_always_fails():
    """검증 인프라 장애(예외/미검출)는 실패 판정이 아니라 '검증 불가'다."""
    state = PlaybackState(duration=1000, ended=True)

    await _verify_via_rescrape(_verify_returning(RuntimeError("네트워크 오류")), state, _log)

    assert state.verified is None
    assert state.error is None  # 정상 재생을 실패로 만들지 않는다
    assert state.lms_completion is None


@pytest.mark.asyncio
async def test_verify_retries_until_lms_ledger_reflects_attendance():
    """LMS 원장 반영 지연 흡수 — 첫 두 번은 미반영, 세 번째에 반영."""
    state = PlaybackState(duration=1000, ended=True)
    fn = _verify_returning(("incomplete", "none"), ("incomplete", "none"), ("completed", "attendance"))

    await _verify_via_rescrape(fn, state, _log)

    assert state.verified is True
    assert state.verify_attempts == 3
