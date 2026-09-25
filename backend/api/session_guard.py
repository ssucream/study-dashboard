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
