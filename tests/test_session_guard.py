"""세션 주기 점검(session_guard.check_session_once) 테스트."""

from unittest.mock import AsyncMock, patch

import pytest
from backend.api import session_guard
from backend.api.state import app_state, scraper_lock


class _FakeScraper:
    def __init__(self, username="", password=""):
        self.username = username
        self.password = password
        self.page = AsyncMock()
        self.closed_pages = []
        self.close = AsyncMock()

    async def new_page(self):
        return self.page

    async def close_page(self, page):
        self.closed_pages.append(page)


@pytest.fixture(autouse=True)
def reset_state():
    yield
    app_state.scraper = None
    app_state.user_id = ""


def _patch_login(needs_login: bool, relogin_ok: bool = True):
    return (
        patch("src.auth.login._needs_login", AsyncMock(return_value=needs_login)),
        patch("src.auth.login.ensure_logged_in", AsyncMock(return_value=relogin_ok)),
    )


async def test_alive_session_refreshes_saved_cookie_and_keeps_login():
    scraper = _FakeScraper()
    app_state.scraper = scraper
    needs, relogin = _patch_login(needs_login=False)
    with needs, relogin, patch("backend.api.routes.auto._snapshot_session", AsyncMock()) as snap:
        await session_guard.check_session_once()
    snap.assert_awaited_once()
    assert app_state.scraper is scraper
    assert scraper.closed_pages == [scraper.page]


async def test_expired_cookie_only_session_forces_logout_and_notifies():
    """학번/비밀번호 없는(쿠키 복원) 세션이 만료되면 사용자가 접속하지 않아도 로그아웃·알림한다."""
    scraper = _FakeScraper()
    app_state.scraper = scraper
    needs, relogin = _patch_login(needs_login=True)
    with needs, relogin, patch.object(session_guard, "force_logout_on_session_loss", AsyncMock()) as logout:
        await session_guard.check_session_once()
    logout.assert_awaited_once_with(scraper)
    assert scraper.closed_pages == [scraper.page]


async def test_expired_session_with_credentials_relogins_instead_of_logout():
    scraper = _FakeScraper(username="20260001", password="pw")
    app_state.scraper = scraper
    needs, relogin = _patch_login(needs_login=True, relogin_ok=False)
    with needs, relogin as ensure, patch.object(session_guard, "force_logout_on_session_loss", AsyncMock()) as logout:
        await session_guard.check_session_once()
    ensure.assert_awaited_once()
    logout.assert_not_awaited()
    assert app_state.scraper is scraper


async def test_skips_when_logged_out_or_scraper_busy():
    with patch("src.auth.login._needs_login", AsyncMock()) as needs:
        await session_guard.check_session_once()  # 로그아웃 상태
        scraper = _FakeScraper()
        app_state.scraper = scraper
        async with scraper_lock:
            await session_guard.check_session_once()  # 자동 사이클이 브라우저 사용 중
    needs.assert_not_awaited()
    assert scraper.closed_pages == []


async def test_navigation_error_does_not_logout():
    scraper = _FakeScraper()
    scraper.page.goto.side_effect = TimeoutError("network")
    app_state.scraper = scraper
    with patch.object(session_guard, "force_logout_on_session_loss", AsyncMock()) as logout:
        with pytest.raises(TimeoutError):
            await session_guard.check_session_once()
    logout.assert_not_awaited()
    assert scraper.closed_pages == [scraper.page]


async def _run_watchdog(monkeypatch, enabled: str, interval_minutes: int) -> int:
    import asyncio

    from src.config import Config

    calls = 0

    async def fake_check():
        nonlocal calls
        calls += 1

    monkeypatch.setattr(session_guard, "check_session_once", fake_check)
    monkeypatch.setattr(Config, "SESSION_CHECK_ENABLED", enabled)
    monkeypatch.setattr(Config, "get_session_check_interval_minutes", classmethod(lambda cls: interval_minutes))
    task = asyncio.create_task(session_guard.session_watchdog_loop(tick=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return calls


async def test_watchdog_runs_when_enabled_and_interval_elapsed(monkeypatch):
    assert await _run_watchdog(monkeypatch, "true", 0) > 0


async def test_watchdog_skips_when_disabled(monkeypatch):
    assert await _run_watchdog(monkeypatch, "false", 0) == 0


async def test_watchdog_waits_for_interval(monkeypatch):
    assert await _run_watchdog(monkeypatch, "true", 30) == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("30", 30), ("90", 90), ("360", 360), ("0", 30), ("15", 30), ("45", 30), ("400", 360), ("abc", 30), ("", 30)],
)
def test_normalize_session_check_interval(raw, expected):
    from src.config import normalize_session_check_interval

    assert normalize_session_check_interval(raw) == expected
