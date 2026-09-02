import asyncio
import logging
from contextlib import suppress

from backend.api.state import app_state, scraper_lock
from backend.api.task_manager import task_manager
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import event_log

logger = logging.getLogger(__name__)
router = APIRouter()

_LOGIN_TIMEOUT_SECONDS = 45
_CLOSE_TIMEOUT_SECONDS = 10


class LoginRequest(BaseModel):
    user_id: str
    password: str


async def _close_scraper(scraper) -> None:
    """Playwright 정리를 bounded timeout 안에서 시도한다."""
    with suppress(Exception):
        await asyncio.wait_for(scraper.close(), timeout=_CLOSE_TIMEOUT_SECONDS)


def _consume_task_exception(task: asyncio.Task) -> None:
    """Background task 예외를 회수해 'never retrieved' 경고를 방지한다."""
    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def _start_scraper_with_timeout(scraper) -> None:
    """로그인 시작을 timeout으로 감싸되, 취소 완료를 기다리느라 응답을 지연하지 않는다."""
    task = asyncio.create_task(scraper.start())
    done, pending = await asyncio.wait({task}, timeout=_LOGIN_TIMEOUT_SECONDS)
    if pending:
        task.cancel()
        task.add_done_callback(_consume_task_exception)
        cleanup_task = asyncio.create_task(_close_scraper(scraper))
        cleanup_task.add_done_callback(_consume_task_exception)
        raise TimeoutError
    await next(iter(done))


@router.post("/login")
async def login(req: LoginRequest):
    from src.scraper.course_scraper import CourseScraper

    # 진행 중인 스크래핑(자동 모드 사이클 등)이 있으면 끝나기를 기다린 뒤 교체한다.
    # 락 없이 이전 scraper를 닫으면 실행 중이던 fetch_all_details가 TargetClosedError로
    # 끊겨 과목 상세 일부가 유실된다.
    scraper = CourseScraper(username=req.user_id, password=req.password)
    try:
        async with scraper_lock:
            if app_state.scraper:
                await _close_scraper(app_state.scraper)
                app_state.scraper = None
            await _start_scraper_with_timeout(scraper)
    except TimeoutError:
        await _close_scraper(scraper)
        event_log.record_event(
            event_type="auth",
            action="login",
            status="failed",
            actor_user_id=event_log.mask_user_id(req.user_id),
            error_code="timeout",
            error_message="로그인 시간이 초과되었습니다.",
        )
        raise HTTPException(
            status_code=504,
            detail="로그인 시간이 초과되었습니다. 계정 정보를 확인하거나 잠시 후 다시 시도하세요.",
        ) from None
    except RuntimeError:
        await _close_scraper(scraper)
        event_log.record_event(
            event_type="auth",
            action="login",
            status="failed",
            actor_user_id=event_log.mask_user_id(req.user_id),
            error_code="invalid_credentials",
            error_message="로그인 실패. 학번/비밀번호를 확인하세요.",
        )
        raise HTTPException(status_code=401, detail="로그인 실패. 학번/비밀번호를 확인하세요.") from None
    except Exception as e:
        await _close_scraper(scraper)
        event_log.record_event(
            event_type="auth",
            action="login",
            status="failed",
            actor_user_id=event_log.mask_user_id(req.user_id),
            error_code=type(e).__name__,
            error_message=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e)) from e

    app_state.scraper = scraper
    app_state.user_id = req.user_id
    app_state.courses = []
    app_state.details = []

    from src.config import Config

    Config.set_session_credentials(req.user_id, req.password)

    event_log.record_event(
        event_type="auth",
        action="login",
        status="success",
        actor_user_id=event_log.mask_user_id(req.user_id),
        message="웹 로그인 성공",
    )

    # 로그아웃/백엔드 재시작으로 세션이 끊겼어도 자동 모드가 켜져 있던 상태였다면 재개한다.
    with suppress(Exception):
        from backend.api.routes.auto import resume_persisted_auto

        if resume_persisted_auto():
            logger.info("로그인 후 자동 모드 자동 재개 (user=%s)", event_log.mask_user_id(req.user_id))
            event_log.record_event(
                event_type="auto",
                action="resume",
                status="success",
                actor_user_id=event_log.mask_user_id(req.user_id),
                message="로그인 시 자동 모드 자동 재개",
            )

    return {"success": True, "user_id": req.user_id}


@router.post("/logout")
async def logout():
    user_id = app_state.user_id or None
    if app_state.play_task_id:
        await task_manager.cancel(app_state.play_task_id)
        app_state.play_task_id = None
    elif app_state.play_task and not app_state.play_task.done():
        app_state.play_task.cancel()
    if app_state.auto.task_id:
        await task_manager.cancel(app_state.auto.task_id)
        app_state.auto.task_id = None
    elif app_state.auto.task and not app_state.auto.task.done():
        app_state.auto.task.cancel()

    # cancel()이 timeout으로 포기했다면 task가 아직 app_state.scraper._page를 참조하며 실행 중일 수 있다.
    # 그 상태에서 scraper를 닫고 None으로 비우면 살아있는 task가 원본 AttributeError로 죽는다.
    # 이 경우 scraper 정리는 건너뛰고 다음 로그인(login())이 남은 scraper를 닫도록 미룬다.
    task_still_running = (app_state.play_task and not app_state.play_task.done()) or (
        app_state.auto.task and not app_state.auto.task.done()
    )
    if app_state.scraper and not task_still_running:
        await app_state.scraper.close()
        app_state.scraper = None

    app_state.user_id = ""
    app_state.courses = []
    app_state.details = []
    app_state.is_playing = False
    app_state.current_lecture_title = ""
    app_state.current_lecture_url = ""
    app_state.current_week_label = ""
    app_state.current_course_name = ""
    app_state.current_course_id = ""
    app_state.auto.enabled = False

    from src.config import Config

    Config.clear_session_credentials()
    # 로그아웃은 자동 모드 지속 상태를 끄지 않는다.
    # 한 번 켠 자동 모드는 '자동 모드 중지'를 누르기 전까지 유지되며,
    # 로그아웃/백엔드 재시작 후 재로그인 시 자동으로 재개된다. (학번/비밀번호는
    # 메모리 전용이라 로그아웃 상태에서 루프를 계속 돌릴 수는 없다.)

    event_log.record_event(
        event_type="auth",
        action="logout",
        status="success",
        actor_user_id=user_id,
        message="웹 로그아웃 성공",
    )

    return {"success": True}


@router.get("/status")
async def status():
    return {
        "authenticated": app_state.scraper is not None,
        "user_id": app_state.user_id,
    }
