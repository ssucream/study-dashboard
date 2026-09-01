"""background_player의 출석 반영 검증 로직 테스트."""

import json

import pytest

from src.player.background_player import (
    _ATTENDANCE_MIN_RATIO,
    PlaybackState,
    _confirm_lms_attendance,
    _extract_watched_seconds,
)


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


def test_extract_watched_seconds_from_viewer_url_endat():
    data = {"viewer_url": "https://commons.ssu.ac.kr/em/x?startat=0.00&endat=1780.50&TargetUrl=y"}
    assert _extract_watched_seconds(data) == pytest.approx(1780.5)


def test_extract_watched_seconds_from_explicit_field():
    data = {"item_content_data": {"duration": 1800}, "attendance": {"cumulative_second": 1700}}
    assert _extract_watched_seconds(data) == 1700.0


def test_extract_watched_seconds_prefers_viewer_url_over_ambiguous():
    data = {
        "viewer_url": "x?endat=1500.00",
        "current_time": 10,  # 모호한 필드 — 무시돼야 함
    }
    assert _extract_watched_seconds(data) == 1500.0


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
