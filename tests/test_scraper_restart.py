"""CourseScraper close()→start() 브라우저 재시작이 세션 쿠키를 이어받는지 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scraper.course_scraper import CourseScraper

COOKIES = {"cookies": [{"name": "canvas_session", "value": "abc"}], "origins": []}


def _fake_browser(storage_state_value):
    context = MagicMock()
    context.storage_state = AsyncMock(return_value=storage_state_value)
    page = MagicMock(url="https://canvas.ssu.ac.kr/")
    page.goto = AsyncMock()
    browser = MagicMock()
    browser.close = AsyncMock()
    return context, page, browser


@pytest.fixture
def patched_playwright():
    pw = MagicMock()
    pw.stop = AsyncMock()
    starter = MagicMock()
    starter.start = AsyncMock(return_value=pw)
    with patch("src.scraper.course_scraper.async_playwright", return_value=starter):
        yield


async def test_restart_reuses_session_cookie_for_cookie_only_session(patched_playwright):
    """학번/비밀번호 없는 쿠키 복원 세션도 재시작 후 같은 쿠키로 브라우저를 연다 (재시작마다 멈추던 버그)."""
    scraper = CourseScraper(username="", password="")
    received = []

    async def fake_setup(storage_state=None):
        received.append(storage_state)
        context, page, browser = _fake_browser(COOKIES)
        scraper._context = context
        return page, browser

    with (
        patch.object(scraper, "_setup_browser", side_effect=fake_setup),
        patch("src.scraper.course_scraper._needs_login", AsyncMock(return_value=False)),
    ):
        await scraper.start(storage_state=COOKIES)
        await scraper.close()
        await scraper.start()  # 자동 모드의 브라우저 재시작 경로

    assert received == [COOKIES, COOKIES]


async def test_expired_cookie_on_restart_still_raises_without_credentials(patched_playwright):
    scraper = CourseScraper(username="", password="")

    async def fake_setup(storage_state=None):
        context, page, browser = _fake_browser(COOKIES)
        scraper._context = context
        return page, browser

    with (
        patch.object(scraper, "_setup_browser", side_effect=fake_setup),
        patch("src.scraper.course_scraper._needs_login", AsyncMock(side_effect=[False, True])),
    ):
        await scraper.start(storage_state=COOKIES)
        await scraper.close()
        with pytest.raises(RuntimeError, match="세션이 만료"):
            await scraper.start()
