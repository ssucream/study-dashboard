"""쿠키만으로 복원된 세션이 실행 도중 만료되는 경우를 감지하고 강제 로그아웃 처리한다.

백엔드 재시작 후 `_resume_auto_from_saved_session()`(backend/main.py)이 저장된 세션
쿠키만으로 `CourseScraper(username="", password="")`를 복원하는 경우가 있다. 그 시점엔
쿠키가 유효해 정상 동작하지만, 시간이 지나 LMS 세션이 만료되면 자동 재로그인에 필요한
학번/비밀번호가 메모리에 없어 매 요청마다 같은 RuntimeError가 반복된다. 이 상태는
로그인 화면 없이 스스로 회복할 수 없으므로, 조용히 반복 실패시키는 대신 명확하게
로그아웃 상태로 전환해 사용자가 다시 로그인하도록 안내한다.
"""

import asyncio
import logging
from contextlib import suppress

from backend.api.state import app_state

logger = logging.getLogger(__name__)

_UNRECOVERABLE_MARKER = "재로그인 실패"


def is_unrecoverable_session_loss(scraper, exc: Exception) -> bool:
    """자격증명 없는(쿠키 전용) 세션이 만료되어 자동 재로그인도 실패한 경우인지 판별한다.

    학번/비밀번호가 있는 세션의 재로그인 실패는 여기 해당하지 않는다 — SSO 장애 등
    일시적 원인일 수 있어, 기존처럼 에러만 남기고 다음 시도에서 회복할 기회를 준다.
    """
    if scraper is None or not isinstance(exc, RuntimeError):
        return False
    if getattr(scraper, "username", "") or getattr(scraper, "password", ""):
        return False
    return _UNRECOVERABLE_MARKER in str(exc)


async def force_logout_on_session_loss(scraper) -> None:
    """세션을 정리하고 로그아웃 상태로 전환한 뒤 텔레그램으로 알린다.

    자동 모드 지속 상태(DB의 AUTO_ENABLED)는 건드리지 않는다 — logout()과 동일하게,
    다음 로그인 시 `resume_persisted_auto()`가 자동 모드를 재개할 수 있어야 한다.
    """
    from src import event_log
    from src.config import Config

    masked_user = event_log.mask_user_id(app_state.user_id) if app_state.user_id else None

    with suppress(Exception):
        await scraper.close()
    app_state.scraper = None
    app_state.user_id = ""
    app_state.courses = []
    app_state.details = []

    logger.warning("쿠키 기반 세션이 실행 중 만료되어 강제 로그아웃 처리 (user=%s)", masked_user)
    with suppress(Exception):
        event_log.record_event(
            event_type="auth",
            action="session_expired",
            status="failed",
            actor_user_id=masked_user,
            message="쿠키 기반 세션이 실행 중 만료되어 재로그인이 필요합니다.",
        )

    if Config.should_notify("error"):
        from src.notifier import telegram_notifier

        loop = asyncio.get_running_loop()
        with suppress(Exception):
            await loop.run_in_executor(
                None,
                telegram_notifier.notify_session_expired_mid_run,
                Config.TELEGRAM_BOT_TOKEN,
                Config.TELEGRAM_CHAT_ID,
            )


# 세션 상태 주기 점검. 이게 없으면 아무도 LMS에 접근하지 않는 동안(자동 모드 꺼짐·사용자 미접속)
# 세션이 만료돼도 다음 웹 접속 때에야 감지되어 텔레그램 알림이 늦게 온다.
# 부수 효과로 주기적 접속이 LMS 세션을 살려두는 keepalive 역할도 한다.
# 간격·ON/OFF는 설정(SESSION_CHECK_*)에서 읽고, 설정 변경이 1분 안에 반영되도록 1분마다 깨어난다.
_WATCHDOG_TICK_SECONDS = 60


async def check_session_once() -> None:
    """현재 로그인 세션이 살아있는지 확인하고, 복구할 수 없게 만료됐으면 강제 로그아웃·알림한다."""
    from backend.api.state import scraper_lock

    from src.auth.login import _needs_login, ensure_logged_in
    from src.scraper.course_scraper import _DASHBOARD_URL

    scraper = app_state.scraper
    # 스크래핑·브라우저 재시작 중이면 세션을 이미 쓰고 있으므로 이번 회차는 건너뛴다
    if scraper is None or scraper_lock.locked():
        return

    expired = False
    async with scraper_lock:
        if app_state.scraper is not scraper:
            return
        page = await scraper.new_page()
        try:
            await page.goto(_DASHBOARD_URL, wait_until="domcontentloaded", timeout=30_000)
            if not await _needs_login(page):
                from backend.api.routes.auto import _snapshot_session

                await _snapshot_session()
                return
            if scraper.username and scraper.password:
                # 학번/비밀번호가 메모리에 있으면 재로그인으로 회복. 실패는 SSO 장애일 수 있어 다음 회차에 재시도
                if not await ensure_logged_in(page, scraper.username, scraper.password):
                    logger.warning("세션 점검: 자동 재로그인 실패 — 다음 점검에서 재시도")
                return
            expired = True
        finally:
            await scraper.close_page(page)

    if expired:
        await force_logout_on_session_loss(scraper)


async def session_watchdog_loop(tick: float = _WATCHDOG_TICK_SECONDS) -> None:
    from src.config import Config

    loop = asyncio.get_running_loop()
    last_check = loop.time()
    while True:
        await asyncio.sleep(tick)
        if Config.SESSION_CHECK_ENABLED != "true":
            last_check = loop.time()
            continue
        if loop.time() - last_check < Config.get_session_check_interval_minutes() * 60:
            continue
        last_check = loop.time()
        try:
            await check_session_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 네트워크 오류 등 일시적 실패는 로그아웃시키지 않는다
            logger.warning("세션 점검 실패 (다음 점검에서 재시도): %s", e)
