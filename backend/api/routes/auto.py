"""자동 모드 API — 미시청 강의를 스케줄에 따라 자동 재생한다."""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta

from backend.api.auth_dep import require_auth
from backend.api.state import PlaybackProgress, app_state
from backend.api.task_manager import ManagedTask, task_manager
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_SCHEDULE_HOURS = [9, 13, 18, 23]

try:
    from src.config import KST
except Exception:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")


class AutoStartRequest(BaseModel):
    schedule_hours: list[int] = _DEFAULT_SCHEDULE_HOURS


def _next_schedule_time(schedule_hours: list[int]) -> datetime:
    now = datetime.now(KST)
    today = [now.replace(hour=h, minute=0, second=0, microsecond=0) for h in sorted(schedule_hours)]
    for t in today:
        if t > now:
            return t
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=sorted(schedule_hours)[0], minute=0, second=0, microsecond=0)


async def _run_post_play_pipeline(course, lec) -> None:
    """재생 완료 후 다운로드 → STT → 요약 → 텔레그램 파이프라인을 실행한다.

    Config 설정에 따라 각 단계를 선택적으로 실행한다.
    """
    from pathlib import Path

    from src.config import Config
    from src.downloader.pipeline import DownloadUnsupportedError, run_download_from_config
    from src.notifier import telegram_notifier

    telegram_enabled = Config.TELEGRAM_ENABLED == "true"
    bot_token = Config.TELEGRAM_BOT_TOKEN or ""
    chat_id = Config.TELEGRAM_CHAT_ID or ""

    def _tg_ok() -> bool:
        return telegram_enabled and bool(bot_token) and bool(chat_id)

    loop = asyncio.get_running_loop()

    # 재생 완료 텔레그램 알림
    if _tg_ok():
        with suppress(Exception):
            await loop.run_in_executor(
                None,
                telegram_notifier.notify_playback_complete,
                bot_token,
                chat_id,
                course.long_name,
                lec.week_label,
                lec.title,
            )

    if Config.DOWNLOAD_ENABLED != "true" or Config.AUTO_DOWNLOAD_AFTER_PLAY != "true":
        return

    def _on_stage(stage: str, message: str, pct: float | None = None) -> None:
        app_state.auto.pipeline_stage = message

    try:
        app_state.auto.pipeline_stage = "다운로드 준비 중..."
        result = await run_download_from_config(
            page=app_state.scraper._page,
            lecture_url=lec.full_url,
            lecture_title=lec.title,
            week_label=lec.week_label,
            course_name=course.long_name,
            on_stage=_on_stage,
        )

        # 요약 완료 시 텔레그램으로 요약 전송
        summary_result = result.get("summary") or {}
        if _tg_ok() and summary_result.get("status") == "completed":
            summary_path_str = summary_result.get("summary_path", "")
            if summary_path_str:
                summary_path = Path(summary_path_str)
                if summary_path.is_file():
                    app_state.auto.pipeline_stage = "텔레그램으로 요약 전송 중..."
                    summary_text = summary_path.read_text(encoding="utf-8")
                    auto_delete: list[Path] = []
                    if Config.TELEGRAM_AUTO_DELETE == "true":
                        for f in result.get("files", []):
                            if f.get("deleted") != "true":
                                p = Path(f["path"])
                                if p.exists():
                                    auto_delete.append(p)
                    with suppress(Exception):
                        await loop.run_in_executor(
                            None,
                            telegram_notifier.notify_summary_complete,
                            bot_token,
                            chat_id,
                            course.long_name,
                            lec.week_label,
                            lec.title,
                            summary_text,
                            summary_path,
                            auto_delete or None,
                        )

    except DownloadUnsupportedError:
        if _tg_ok():
            with suppress(Exception):
                await loop.run_in_executor(
                    None,
                    telegram_notifier.notify_download_unsupported,
                    bot_token,
                    chat_id,
                    course.long_name,
                    lec.week_label,
                    lec.title,
                )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        app_state.auto.error = f"파이프라인 오류: {e}"
        if _tg_ok():
            with suppress(Exception):
                await loop.run_in_executor(
                    None,
                    telegram_notifier.notify_auto_error,
                    bot_token,
                    chat_id,
                    course.long_name,
                    lec.week_label,
                    lec.title,
                    str(e),
                )
    finally:
        app_state.auto.pipeline_stage = ""


async def _run_auto_cycle() -> None:
    """미시청 강의를 한 사이클 순차 재생한다."""
    from backend.api.routes.player import _mark_lecture_completed, _write_playback_log

    from src.player.background_player import play_lecture

    if not app_state.scraper:
        app_state.auto.error = "로그인 상태가 아닙니다."
        app_state.auto.enabled = False
        return

    app_state.auto.error = None

    # 강의 목록 갱신
    try:
        courses = await app_state.scraper.fetch_courses()
        details = await app_state.scraper.fetch_all_details(courses)
        app_state.courses = courses
        app_state.details = details
    except asyncio.CancelledError:
        raise
    except Exception as e:
        app_state.auto.error = f"강의 목록 갱신 실패: {e}"
        return

    # 마감 임박 알림 (텔레그램 설정 시)
    from src.config import Config

    if Config.TELEGRAM_ENABLED == "true" and Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID:
        from src.notifier.deadline_checker import check_and_notify_deadlines

        loop = asyncio.get_running_loop()
        with suppress(Exception):
            await loop.run_in_executor(
                None,
                check_and_notify_deadlines,
                courses,
                details,
                Config.TELEGRAM_BOT_TOKEN,
                Config.TELEGRAM_CHAT_ID,
            )

    # 미시청 강의 수집
    pending: list[tuple] = []
    for course, detail in zip(courses, details, strict=False):
        if detail is None:
            continue
        for lec in detail.all_video_lectures:
            if lec.needs_watch:
                pending.append((course, lec))

    if not pending:
        app_state.auto.current_lecture = ""
        app_state.auto.current_course = ""
        next_time = _next_schedule_time(app_state.auto.schedule_hours)
        app_state.auto.next_run_at = next_time.strftime("%H:%M")
        return

    for idx, (course, lec) in enumerate(pending):
        if not app_state.auto.enabled:
            break

        # 5강의마다 중간 브라우저 재시작 (Chromium 힙 누적 억제)
        if idx > 0 and idx % 5 == 0 and app_state.scraper:
            try:
                await app_state.scraper.close()
                await app_state.scraper.start()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                app_state.auto.error = f"중간 브라우저 재시작 실패: {e}"

        # 수동 재생 중이면 완료 대기
        while app_state.is_playing and app_state.auto.enabled:
            await asyncio.sleep(2)

        if not app_state.auto.enabled:
            break

        app_state.auto.current_course = course.long_name
        app_state.auto.current_lecture = lec.title

        log_buffer: list[str] = []
        app_state.current_lecture_title = lec.title
        app_state.current_lecture_url = lec.full_url
        app_state.current_week_label = lec.week_label
        app_state.current_course_name = course.long_name
        app_state.current_course_id = course.id
        app_state.playback = PlaybackProgress(status="playing")
        app_state.is_playing = True

        def _on_progress(s):
            app_state.playback.current = s.current
            app_state.playback.duration = s.duration
            app_state.playback.ended = s.ended
            app_state.playback.error = s.error

        try:
            final_state = await play_lecture(
                app_state.scraper._page,
                lec.full_url,
                on_progress=_on_progress,
                debug=True,
                log_fn=log_buffer.append,
            )
            _on_progress(final_state)

            if final_state.cancelled:
                # 재생이 취소됨 → 이번 세션의 자동 루프는 멈추되, 지속 상태(DB)는 건드리지
                # 않는다. 자동 모드는 '자동 모드 중지'로만 완전히 꺼지고, 재로그인 시 재개된다.
                app_state.playback.status = "stopped"
                app_state.auto.enabled = False
                break
            elif final_state.error:
                app_state.playback.status = "error"
                app_state.playback.log_path = _write_playback_log(
                    lec.title, lec.full_url, final_state.error, log_buffer
                )
                # 재생은 끝났지만 LMS에 출석이 반영되지 않은 경우 — 완료 처리하지 않고
                # 다음 사이클에 재시도된다. 사용자가 알 수 있도록 로그·알림을 남긴다.
                if final_state.lms_progress_ratio is not None:
                    logger.warning(
                        "출석 미반영: %s / %s — LMS 진도 %.0f%% (다음 사이클 재시도)",
                        course.long_name,
                        lec.title,
                        final_state.lms_progress_ratio * 100,
                    )
                    from src import event_log

                    with suppress(Exception):
                        event_log.record_event(
                            event_type="player",
                            action="attendance_not_recorded",
                            status="failed",
                            actor_user_id=app_state.user_id or None,
                            target_type="lecture",
                            course_id=course.id,
                            course_name=course.long_name,
                            lecture_title=lec.title,
                            week_label=lec.week_label,
                            error_code="attendance_not_recorded",
                            error_message=final_state.error,
                            log_path=app_state.playback.log_path,
                            metadata={"lms_progress_ratio": round(final_state.lms_progress_ratio, 3)},
                        )
                    from src.config import Config
                    from src.notifier import telegram_notifier

                    if Config.TELEGRAM_ENABLED == "true" and Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID:
                        loop = asyncio.get_running_loop()
                        with suppress(Exception):
                            await loop.run_in_executor(
                                None,
                                telegram_notifier.notify_auto_error,
                                Config.TELEGRAM_BOT_TOKEN,
                                Config.TELEGRAM_CHAT_ID,
                                course.long_name,
                                lec.week_label,
                                lec.title,
                                final_state.error,
                            )
            elif final_state.ended:
                app_state.playback.status = "completed"
                updated = _mark_lecture_completed(course.id, lec.full_url)
                if not updated:
                    app_state.playback.refresh_recommended = True
                app_state.auto.processed_count += 1
                await _run_post_play_pipeline(course, lec)
            else:
                app_state.playback.status = "stopped"

        except asyncio.CancelledError:
            app_state.playback.status = "stopped"
            app_state.is_playing = False
            raise
        except Exception as e:
            app_state.playback.status = "error"
            app_state.playback.error = str(e)
            app_state.playback.log_path = _write_playback_log(lec.title, lec.full_url, str(e), log_buffer)
        finally:
            app_state.is_playing = False
            with suppress(Exception):
                await app_state.scraper._page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)

        await asyncio.sleep(1)

    app_state.auto.current_course = ""
    app_state.auto.current_lecture = ""


async def _auto_loop() -> None:
    """자동 모드 백그라운드 루프 — 즉시 1회 실행 후 스케줄 대기."""
    first_run = True
    try:
        while app_state.auto.enabled:
            if not first_run:
                next_time = _next_schedule_time(app_state.auto.schedule_hours)
                app_state.auto.next_run_at = next_time.strftime("%H:%M")
                now = datetime.now(KST)
                wait_sec = max(0.0, (next_time - now).total_seconds())
                try:
                    await asyncio.sleep(wait_sec)
                except asyncio.CancelledError:
                    return
            first_run = False

            if not app_state.auto.enabled:
                return

            await _run_auto_cycle()

            # 사이클 완료 후 브라우저 재시작 (Chromium 메모리 누적 방지)
            if app_state.auto.enabled and app_state.scraper:
                try:
                    await app_state.scraper.close()
                    await app_state.scraper.start()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    app_state.auto.error = f"브라우저 재시작 실패: {e}"
                    app_state.auto.enabled = False
                    return
    finally:
        app_state.auto.enabled = False
        app_state.auto.current_course = ""
        app_state.auto.current_lecture = ""
        app_state.auto.task = None
        app_state.auto.task_id = None


def _launch_auto_loop(hours: list[int]) -> str:
    """자동 모드 백그라운드 루프를 시작하고 task_id를 반환한다.

    라우트(`auto_start`)와 로그인 시 복원(`resume_persisted_auto`) 양쪽에서 재사용한다.
    """
    app_state.auto.enabled = True
    app_state.auto.schedule_hours = hours
    app_state.auto.processed_count = 0
    app_state.auto.error = None
    app_state.auto.next_run_at = ""

    async def run(managed: ManagedTask):
        managed.update(stage="auto_loop", message="자동 모드가 실행 중입니다.")
        await _auto_loop()
        if app_state.auto.error:
            managed.update(status="failed", stage="error", error=app_state.auto.error)
        return {"processed_count": app_state.auto.processed_count}

    managed = task_manager.create("auto", run, metadata={"schedule_hours": hours})
    app_state.auto.task = managed.task
    app_state.auto.task_id = managed.id
    return managed.id


def resume_persisted_auto() -> bool:
    """백엔드 재시작 후 로그인 시, DB에 저장된 자동 모드가 켜져 있으면 재개한다.

    재개했으면 True, 아니면(비활성 상태이거나 이미 실행 중) False.
    """
    from src.config import Config

    if Config.AUTO_ENABLED != "true":
        logger.info("자동 모드 복원 건너뜀 — 지속 상태 OFF (AUTO_ENABLED=%r)", Config.AUTO_ENABLED)
        return False
    if app_state.auto.enabled and app_state.auto.task and not app_state.auto.task.done():
        logger.info("자동 모드 복원 건너뜀 — 이미 실행 중")
        return False
    hours = Config.get_auto_schedule_hours()
    logger.info("자동 모드 복원 — 로그인 시 DB 지속 상태가 ON, 스케줄=%s 로 재개", hours)
    _launch_auto_loop(hours)
    return True


@router.get("/status")
async def auto_status():
    require_auth()
    a = app_state.auto
    return {
        "enabled": a.enabled,
        "schedule_hours": a.schedule_hours,
        "current_course": a.current_course or None,
        "current_lecture": a.current_lecture or None,
        "processed_count": a.processed_count,
        "next_run_at": a.next_run_at or None,
        "error": a.error,
        "task_id": a.task_id,
        "pipeline_stage": a.pipeline_stage or None,
    }


@router.post("/start")
async def auto_start(req: AutoStartRequest):
    require_auth()

    hours = sorted(set(req.schedule_hours))
    if not hours or any(h < 0 or h > 23 for h in hours):
        raise HTTPException(status_code=422, detail="스케줄 시간은 0~23 사이 값이어야 합니다.")
    if len(hours) > 6:
        raise HTTPException(status_code=422, detail="스케줄은 최대 6회까지 설정할 수 있습니다.")

    if app_state.auto.enabled and app_state.auto.task and not app_state.auto.task.done():
        raise HTTPException(status_code=409, detail="이미 자동 모드가 실행 중입니다.")

    task_id = _launch_auto_loop(hours)

    from src.config import Config

    # 백엔드 재시작 후 재로그인 시 복원할 수 있도록 지속 상태를 DB에 저장
    with suppress(Exception):
        Config.save_auto_state(True, hours)

    # 미설정 기능 소프트 경고 (시작을 막지는 않음)
    warnings: list[str] = []
    if Config.DOWNLOAD_ENABLED != "true" or Config.AUTO_DOWNLOAD_AFTER_PLAY != "true":
        warnings.append("재생 완료 후 자동 다운로드가 비활성화되어 있어 STT·AI 요약이 실행되지 않습니다.")
    elif Config.STT_ENABLED != "true":
        warnings.append("STT가 비활성화되어 있어 AI 요약이 실행되지 않습니다.")
    elif Config.AI_ENABLED != "true":
        warnings.append("AI 요약이 비활성화되어 있습니다.")
    if not (Config.TELEGRAM_ENABLED == "true" and Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID):
        warnings.append("텔레그램 알림이 설정되지 않아 진행 상황을 알림으로 받을 수 없습니다.")

    return {"started": True, "schedule_hours": hours, "task_id": task_id, "warnings": warnings}


class AutoScheduleUpdate(BaseModel):
    schedule_hours: list[int]


@router.put("/schedule")
async def update_schedule(req: AutoScheduleUpdate):
    require_auth()
    hours = sorted(set(req.schedule_hours))
    if not hours or any(h < 0 or h > 23 for h in hours):
        raise HTTPException(status_code=422, detail="스케줄 시간은 0~23 사이 값이어야 합니다.")
    if len(hours) > 6:
        raise HTTPException(status_code=422, detail="스케줄은 최대 6회까지 설정할 수 있습니다.")
    app_state.auto.schedule_hours = hours
    return {"updated": True, "schedule_hours": hours}


@router.post("/stop")
async def auto_stop():
    require_auth()
    app_state.auto.enabled = False

    # 사용자가 명시적으로 중지 → 재로그인해도 복원하지 않도록 지속 상태를 끈다
    from src.config import Config

    with suppress(Exception):
        Config.save_auto_state(False)

    if app_state.auto.task_id:
        await task_manager.cancel(app_state.auto.task_id)
    elif app_state.auto.task and not app_state.auto.task.done():
        app_state.auto.task.cancel()
        with suppress(Exception):
            await asyncio.wait_for(asyncio.shield(app_state.auto.task), timeout=3.0)
    app_state.auto.task = None
    app_state.auto.task_id = None
    app_state.auto.current_course = ""
    app_state.auto.current_lecture = ""
    return {"stopped": True}
