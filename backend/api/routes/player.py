import asyncio
from pathlib import Path

from backend.api.state import PlaybackProgress, app_state
from backend.api.task_manager import ManagedTask, task_manager
from backend.api.validators import validate_lecture_url
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from src import event_log

router = APIRouter()


class PlayRequest(BaseModel):
    course_id: str
    lecture_url: str
    lecture_title: str
    week_label: str = ""

    _validate_lecture_url = field_validator("lecture_url")(validate_lecture_url)


def _require_auth() -> None:
    if not app_state.scraper:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")


def _sync_progress(state) -> None:
    app_state.playback.current = state.current
    app_state.playback.duration = state.duration
    app_state.playback.ended = state.ended
    app_state.playback.error = state.error


def _mark_lecture_completed(course_id: str, lecture_url: str) -> bool:
    """Cached course details에서 완료된 강의를 즉시 반영한다."""
    course_idx = next((i for i, course in enumerate(app_state.courses) if course.id == course_id), None)
    if course_idx is None or course_idx >= len(app_state.details):
        return False

    detail = app_state.details[course_idx]
    if not detail:
        return False

    for week in detail.weeks:
        for lecture in week.lectures:
            if lecture.full_url == lecture_url or lecture.item_url == lecture_url:
                lecture.completion = "completed"
                return True
    return False


def _write_playback_log(title: str, lecture_url: str, error: str, log_buffer: list[str]) -> str | None:
    """웹 재생 실패 로그를 파일로 남기고 경로를 반환한다."""
    try:
        from src.logger import close_error_logger, get_error_logger

        logger, log_path = get_error_logger("web_play")
        logger.info(f"강의: {title}")
        logger.info(f"URL: {lecture_url}")
        logger.info(f"오류: {error}")
        logger.info("--- 재생 로그 ---")
        for line in log_buffer:
            logger.info(line)
        close_error_logger(logger)
        return str(Path(log_path).resolve())
    except Exception:
        return None


async def _notify_playback_complete(course_name: str, week_label: str, lecture_title: str) -> None:
    """텔레그램 재생 완료 알림. 설정 미완성이면 무시."""
    from contextlib import suppress

    from src.config import Config
    from src.notifier import telegram_notifier

    if not (Config.TELEGRAM_ENABLED == "true" and Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID):
        return
    with suppress(Exception):
        await asyncio.get_running_loop().run_in_executor(
            None,
            telegram_notifier.notify_playback_complete,
            Config.TELEGRAM_BOT_TOKEN,
            Config.TELEGRAM_CHAT_ID,
            course_name,
            week_label,
            lecture_title,
        )


async def _notify_playback_error(course_name: str, week_label: str, lecture_title: str, failed: bool = True) -> None:
    """텔레그램 재생 실패 알림. 설정 미완성이면 무시."""
    from contextlib import suppress

    from src.config import Config
    from src.notifier import telegram_notifier

    if not (Config.TELEGRAM_ENABLED == "true" and Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID):
        return
    with suppress(Exception):
        await asyncio.get_running_loop().run_in_executor(
            None,
            telegram_notifier.notify_playback_error,
            Config.TELEGRAM_BOT_TOKEN,
            Config.TELEGRAM_CHAT_ID,
            course_name,
            week_label,
            lecture_title,
            failed,
        )


def _schedule_auto_download(req: PlayRequest, course, play_task_id: str) -> None:
    """재생 완료 후 자동 다운로드 task를 백그라운드로 생성한다."""
    from src.config import Config
    from src.downloader.pipeline import download_lecture_media

    async def run(managed: ManagedTask) -> dict:
        def on_stage(stage: str, message: str, progress_pct: float | None = None) -> None:
            managed.update(stage=stage, message=message, progress_pct=progress_pct)

        return await download_lecture_media(
            page=app_state.scraper._page,
            lecture_url=req.lecture_url,
            lecture_title=req.lecture_title,
            week_label=req.week_label,
            course_name=course.long_name,
            download_dir=Config.get_download_dir(),
            rule=Config.get_download_rule(),
            stt_enabled=Config.STT_ENABLED == "true",
            stt_model=Config.WHISPER_MODEL or "base",
            stt_language=Config.STT_LANGUAGE or "",
            delete_audio_after_stt=Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE == "true",
            ai_enabled=Config.AI_ENABLED == "true",
            ai_agent=Config.AI_AGENT or "gemini",
            ai_api_key=Config.GOOGLE_API_KEY or "",
            ai_model=Config.GEMINI_MODEL or "",
            summary_prompt_template=Config.get_summary_prompt_template(),
            summary_prompt_extra=Config.SUMMARY_PROMPT_EXTRA or "",
            delete_text_after_summary=Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE == "true",
            on_stage=on_stage,
        )

    managed = task_manager.create(
        "download",
        run,
        metadata={
            "course_id": req.course_id,
            "course_name": course.long_name,
            "lecture_title": req.lecture_title,
            "week_label": req.week_label,
            "source_play_task_id": play_task_id,
        },
    )
    app_state.auto_download_task_id = managed.id


@router.post("/play")
async def start_play(req: PlayRequest):
    _require_auth()
    if app_state.is_playing:
        raise HTTPException(status_code=409, detail="이미 재생 중입니다.")
    if app_state.auto.enabled:
        raise HTTPException(status_code=409, detail="자동 모드 실행 중에는 수동 재생을 시작할 수 없습니다.")

    course = next((c for c in app_state.courses if c.id == req.course_id), None)
    if not course:
        raise HTTPException(status_code=404, detail="과목을 찾을 수 없습니다.")

    from src.player.background_player import PlaybackState, play_lecture

    app_state.current_lecture_title = req.lecture_title
    app_state.current_lecture_url = req.lecture_url
    app_state.current_week_label = req.week_label
    app_state.current_course_name = course.long_name
    app_state.current_course_id = course.id
    app_state.playback = PlaybackProgress(status="playing")
    app_state.auto_download_task_id = None
    app_state.is_playing = True
    log_buffer: list[str] = []

    def on_progress(state: PlaybackState):
        _sync_progress(state)
        if not state.error:
            app_state.playback.status = "playing"

    async def run(managed: ManagedTask):
        managed.update(stage="playing", message=req.lecture_title)
        try:
            final_state = await play_lecture(
                app_state.scraper._page,
                req.lecture_url,
                on_progress=on_progress,
                debug=True,
                log_fn=log_buffer.append,
            )
            _sync_progress(final_state)

            if final_state.error == "사용자 중단":
                app_state.playback.status = "stopped"
                app_state.playback.error = None
                managed.update(status="cancelled", stage="stopped", message="재생이 중지되었습니다.")
                event_log.record_event(
                    event_type="player",
                    action="play_stop",
                    status="cancelled",
                    actor_user_id=app_state.user_id or None,
                    target_type="lecture",
                    course_id=req.course_id,
                    course_name=course.long_name,
                    lecture_title=req.lecture_title,
                    lecture_url=req.lecture_url,
                    week_label=req.week_label,
                    message="재생이 중지되었습니다.",
                    metadata={"task_id": managed.id},
                )
            elif final_state.error:
                app_state.playback.status = "error"
                app_state.playback.log_path = _write_playback_log(
                    req.lecture_title,
                    req.lecture_url,
                    final_state.error,
                    log_buffer,
                )
                managed.update(status="failed", stage="error", error=final_state.error)
                event_log.record_event(
                    event_type="player",
                    action="play_failed",
                    status="failed",
                    actor_user_id=app_state.user_id or None,
                    target_type="lecture",
                    course_id=req.course_id,
                    course_name=course.long_name,
                    lecture_title=req.lecture_title,
                    lecture_url=req.lecture_url,
                    week_label=req.week_label,
                    error_code="playback_error",
                    error_message=final_state.error,
                    log_path=app_state.playback.log_path,
                    metadata={"task_id": managed.id},
                )
                await _notify_playback_error(course.long_name, req.week_label, req.lecture_title)
            elif final_state.ended:
                app_state.playback.status = "completed"
                updated = _mark_lecture_completed(req.course_id, req.lecture_url)
                if not updated:
                    app_state.playback.refresh_recommended = True
                managed.update(result={"playback_status": "completed"})
                event_log.record_event(
                    event_type="player",
                    action="play_complete",
                    status="success",
                    actor_user_id=app_state.user_id or None,
                    target_type="lecture",
                    course_id=req.course_id,
                    course_name=course.long_name,
                    lecture_title=req.lecture_title,
                    lecture_url=req.lecture_url,
                    week_label=req.week_label,
                    message="재생이 완료되었습니다.",
                    metadata={"task_id": managed.id, "cache_updated": updated},
                )
                await _notify_playback_complete(course.long_name, req.week_label, req.lecture_title)
                from src.config import Config

                if Config.DOWNLOAD_ENABLED == "true" and Config.AUTO_DOWNLOAD_AFTER_PLAY == "true":
                    _schedule_auto_download(req, course, managed.id)
            else:
                app_state.playback.status = "stopped"
                managed.update(status="cancelled", stage="stopped", message="재생이 완료되지 않았습니다.")
                event_log.record_event(
                    event_type="player",
                    action="play_stop",
                    status="cancelled",
                    actor_user_id=app_state.user_id or None,
                    target_type="lecture",
                    course_id=req.course_id,
                    course_name=course.long_name,
                    lecture_title=req.lecture_title,
                    lecture_url=req.lecture_url,
                    week_label=req.week_label,
                    message="재생이 완료되지 않았습니다.",
                    metadata={"task_id": managed.id},
                )
                await _notify_playback_error(course.long_name, req.week_label, req.lecture_title, failed=False)
        except asyncio.CancelledError:
            app_state.playback.status = "stopped"
            app_state.playback.error = None
            event_log.record_event(
                event_type="player",
                action="play_stop",
                status="cancelled",
                actor_user_id=app_state.user_id or None,
                target_type="lecture",
                course_id=req.course_id,
                course_name=course.long_name,
                lecture_title=req.lecture_title,
                lecture_url=req.lecture_url,
                week_label=req.week_label,
                message="재생 작업이 취소되었습니다.",
                metadata={"task_id": managed.id},
            )
            raise
        except Exception as e:
            app_state.playback.status = "error"
            app_state.playback.error = str(e)
            app_state.playback.log_path = _write_playback_log(
                req.lecture_title,
                req.lecture_url,
                str(e),
                log_buffer,
            )
            managed.update(status="failed", stage="error", error=str(e))
            event_log.record_event(
                event_type="player",
                action="play_failed",
                status="failed",
                actor_user_id=app_state.user_id or None,
                target_type="lecture",
                course_id=req.course_id,
                course_name=course.long_name,
                lecture_title=req.lecture_title,
                lecture_url=req.lecture_url,
                week_label=req.week_label,
                error_code=type(e).__name__,
                error_message=str(e),
                log_path=app_state.playback.log_path,
                metadata={"task_id": managed.id},
            )
            await _notify_playback_error(course.long_name, req.week_label, req.lecture_title)
        finally:
            app_state.is_playing = False

    managed = task_manager.create(
        "player",
        run,
        metadata={
            "course_id": req.course_id,
            "lecture_title": req.lecture_title,
            "week_label": req.week_label,
        },
    )
    app_state.play_task = managed.task
    app_state.play_task_id = managed.id

    event_log.record_event(
        event_type="player",
        action="play_start",
        status="started",
        actor_user_id=app_state.user_id or None,
        target_type="lecture",
        course_id=req.course_id,
        course_name=course.long_name,
        lecture_title=req.lecture_title,
        lecture_url=req.lecture_url,
        week_label=req.week_label,
        message="재생을 시작했습니다.",
        metadata={"task_id": managed.id},
    )

    return {"started": True, "lecture": req.lecture_title, "task_id": managed.id}


@router.post("/stop")
async def stop_play():
    _require_auth()
    task_id = app_state.play_task_id
    if app_state.play_task_id:
        await task_manager.cancel(app_state.play_task_id)
    elif app_state.play_task and not app_state.play_task.done():
        app_state.play_task.cancel()
    app_state.play_task_id = None
    app_state.is_playing = False
    app_state.playback.status = "stopped"
    app_state.playback.error = None
    event_log.record_event(
        event_type="player",
        action="play_stop_request",
        status="requested",
        actor_user_id=app_state.user_id or None,
        target_type="lecture",
        course_id=app_state.current_course_id or None,
        course_name=app_state.current_course_name or None,
        lecture_title=app_state.current_lecture_title or None,
        lecture_url=app_state.current_lecture_url or None,
        week_label=app_state.current_week_label or None,
        message="재생 중지 요청",
        metadata={"task_id": task_id},
    )
    return {"stopped": True}


@router.get("/status")
async def get_status():
    pb = app_state.playback
    return {
        "is_playing": app_state.is_playing,
        "course_name": app_state.current_course_name,
        "course_id": app_state.current_course_id or None,
        "lecture_title": app_state.current_lecture_title or None,
        "lecture_url": app_state.current_lecture_url or None,
        "week_label": app_state.current_week_label or None,
        "current": pb.current,
        "duration": pb.duration,
        "progress_pct": pb.progress_pct,
        "ended": pb.ended,
        "error": pb.error,
        "status": pb.status,
        "log_path": pb.log_path,
        "refresh_recommended": pb.refresh_recommended,
        "task_id": app_state.play_task_id,
        "auto_download_task_id": app_state.auto_download_task_id,
    }
