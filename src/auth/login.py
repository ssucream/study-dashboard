import asyncio
from contextlib import suppress

from playwright.async_api import Page

_LOGIN_PAGE_TIMEOUT_MS = 10_000
_LOGIN_RESULT_TIMEOUT_SECONDS = 10.0
_LOGIN_FORM_STABLE_SECONDS = 3.0
_LOGIN_ERROR_KEYWORDS = (
    "로그인 실패",
    "올바르지",
    "일치하지",
    "잘못",
    "invalid",
    "incorrect",
    "failed",
)


async def _needs_login(page: Page) -> bool:
    """로그인이 필요한 상태인지 확인한다 (URL + 로그인 버튼 존재 여부).

    Canvas가 미인증 사용자를 'login' 문자열 없는 URL(예: /?)로
    리다이렉트하는 경우에도 올바르게 감지한다.
    """
    if "login" in page.url:
        return True
    with suppress(Exception):
        login_btn = await page.query_selector(".login_btn")
        if login_btn and await login_btn.is_visible():
            return True
    return False


async def _is_login_form_visible(page: Page) -> bool:
    """로그인 폼이 여전히 표시되는지 확인한다."""
    with suppress(Exception):
        user_input = await page.query_selector("input#userid")
        if user_input and await user_input.is_visible():
            return True
    return False


async def _has_login_error_text(page: Page) -> bool:
    """SSO 페이지에 로그인 실패 문구가 표시되는지 확인한다."""
    with suppress(Exception):
        return bool(
            await page.evaluate(
                """
                (keywords) => {
                    const text = (document.body && document.body.innerText || '').toLowerCase();
                    return keywords.some((keyword) => text.includes(keyword.toLowerCase()));
                }
                """,
                list(_LOGIN_ERROR_KEYWORDS),
            )
        )
    return False


async def _wait_for_login_result(page: Page, dialog_seen: asyncio.Event) -> bool:
    """로그인 성공/실패를 짧은 폴링으로 판정한다."""
    deadline = asyncio.get_running_loop().time() + _LOGIN_RESULT_TIMEOUT_SECONDS
    form_visible_since: float | None = None

    while asyncio.get_running_loop().time() < deadline:
        if dialog_seen.is_set():
            return False

        if "canvas.ssu.ac.kr" in page.url and "login" not in page.url:
            with suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=_LOGIN_PAGE_TIMEOUT_MS)
            # 로그인 버튼이 사라졌는지 재확인 (Canvas가 /?로 리다이렉트하는 경우 대비)
            if not await _needs_login(page):
                return True

        if await _has_login_error_text(page):
            return False

        if await _is_login_form_visible(page):
            now = asyncio.get_running_loop().time()
            if form_visible_since is None:
                form_visible_since = now
            elif now - form_visible_since >= _LOGIN_FORM_STABLE_SECONDS:
                return False
        else:
            form_visible_since = None

        await asyncio.sleep(0.25)

    return "canvas.ssu.ac.kr" in page.url and not await _needs_login(page)


async def perform_login(page: Page, username: str, password: str) -> bool:
    """SSO 로그인 처리. 성공 시 True, 실패 시 False 반환."""
    dialog_seen = asyncio.Event()

    def _on_dialog(dialog):
        dialog_seen.set()
        with suppress(Exception):
            task = asyncio.create_task(dialog.accept())
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    page.on("dialog", _on_dialog)
    try:
        # SSU가 추가한 사이트 선택 페이지(canvas-discovery/login.php) 처리:
        # "숭실대학교"(a.btn-ssu-main)를 눌러 실제 로그인 페이지로 진입한다.
        site_select = await page.query_selector("a.btn-ssu-main")
        if site_select:
            await site_select.click()
            with suppress(Exception):
                await page.wait_for_selector(".login_btn a", timeout=_LOGIN_PAGE_TIMEOUT_MS)

        login_button = await page.query_selector(".login_btn a")
        if login_button:
            await login_button.click()
            with suppress(Exception):
                await page.wait_for_selector("input#userid", timeout=_LOGIN_PAGE_TIMEOUT_MS)

        await page.fill("input#userid", username, timeout=_LOGIN_PAGE_TIMEOUT_MS)
        await page.fill("input#pwd", password, timeout=_LOGIN_PAGE_TIMEOUT_MS)

        await page.click("a.btn_login", timeout=_LOGIN_PAGE_TIMEOUT_MS)

        return await _wait_for_login_result(page, dialog_seen)

    except Exception:
        return False
    finally:
        with suppress(Exception):
            page.remove_listener("dialog", _on_dialog)


async def ensure_logged_in(page: Page, username: str, password: str) -> bool:
    """현재 페이지가 로그인이 필요한 상태이면 로그인을 수행."""
    if not await _needs_login(page):
        return True
    return await perform_login(page, username, password)
