"""자동 모드 API — 미시청 강의를 스케줄에 따라 자동 재생한다."""

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta

from backend.api.auth_dep import require_auth
from backend.api.state import PlaybackProgress, app_state, scraper_lock
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


def _make_verify_fn(course, lec):
    """재생 후 LMS 목록 재스크래핑으로 (completion, attendance)를 읽는 콜백을 만든다.

    background_player가 CourseScraper를 직접 import하지 않도록 호출자가 주입한다.
    """

    async def _verify() -> tuple[str, str] | None:
        scraper = app_state.scraper
        if scraper is None:
            return None
        return await scraper.fetch_item_status(course, lec.full_url)

    return _verify


def _log_attendance_not_recorded(course, lec, state) -> None:
    """재스크래핑으로 출석 미반영이 **확정**된 경우만 기록한다.

    action/error_code를 `attendance_not_recorded`로 고정하므로, 일시적 재생 오류를
    여기로 흘리면 Step 5-4의 `verified` 비율 측정 쿼리가 오염된다.
    """
    from backend.api.routes.player import _verification_metadata

    from src import event_log

    ratio = state.lms_progress_ratio
    logger.warning(
        "출석 미반영 확정: %s / %s — completion=%s attendance=%s LMS진도=%s",
        course.long_name,
        lec.title,
        state.lms_completion,
        state.lms_attendance,
        f"{ratio * 100:.0f}%" if ratio is not None else "확인불가",
    )
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
            lecture_url=lec.full_url,
            week_label=lec.week_label,
            error_code="attendance_not_recorded",
            error_message=state.error,
            log_path=app_state.playback.log_path,
            metadata=_verification_metadata(state),
        )


def _log_playback_error(course, lec, state) -> None:
    """일시적 재생 오류(크래시·타임아웃·ErrAlreadyInView 등)를 기록한다.

    출석 미반영과 별개 action(`play_failed`)으로 남겨 측정 쿼리를 분리한다.
    """
    from backend.api.routes.player import _verification_metadata

    from src import event_log

    logger.warning(
        "재생 실패: %s / %s — %s (다음 사이클 재시도)",
        course.long_name,
        lec.title,
        state.error,
    )
    with suppress(Exception):
        event_log.record_event(
            event_type="player",
            action="play_failed",
            status="failed",
            actor_user_id=app_state.user_id or None,
            target_type="lecture",
            course_id=course.id,
            course_name=course.long_name,
            lecture_title=lec.title,
            lecture_url=lec.full_url,
            week_label=lec.week_label,
            error_code="playback_error",
            error_message=state.error,
            log_path=app_state.playback.log_path,
            metadata=_verification_metadata(state),
        )


async def _record_ledger_attempt(course, lec, result: str, error: str | None = None) -> None:
    """재생 결과를 억제 원장에 기록하고, 이번 호출에서 억제되면 1회 알린다."""
    from src import event_log, playback_ledger
    from src.config import Config

    count, suppressed_now = playback_ledger.record_attempt(
        course.id,
        lec.full_url,
        course_name=course.long_name,
        lecture_title=lec.title,
        week_label=lec.week_label,
        result=result,
        error=error,
        max_attempts=Config.get_auto_max_retry(),
    )
    if not suppressed_now:
        return

    logger.warning(
        "재시도 억제: %s / %s — %d회 연속 출석 미반영으로 자동 모드 pending에서 제외",
        course.long_name,
        lec.title,
        count,
    )
    with suppress(Exception):
        event_log.record_event(
            event_type="player",
            action="playback_suppressed",
            status="failed",
            actor_user_id=app_state.user_id or None,
            target_type="lecture",
            course_id=course.id,
            course_name=course.long_name,
            lecture_title=lec.title,
            lecture_url=lec.full_url,
            week_label=lec.week_label,
            error_code="playback_suppressed",
            error_message=error,
            message="반복 실패로 자동 재시도에서 제외했습니다.",
            metadata={"attempt_count": count, "last_result": result},
        )

    # notified 컬럼을 실제 가드로 선점한다 — 백엔드 재시작 후 재억제돼도 중복 발송되지 않는다.
    if Config.should_notify("error") and playback_ledger.claim_notification(course.id, lec.full_url):
        from src.notifier import telegram_notifier

        loop = asyncio.get_running_loop()
        with suppress(Exception):
            await loop.run_in_executor(
                None,
                telegram_notifier.notify_playback_suppressed,
                Config.TELEGRAM_BOT_TOKEN,
                Config.TELEGRAM_CHAT_ID,
                course.long_name,
                lec.week_label,
                lec.title,
                count,
                error or "",
            )


async def _notify_playback_failure(course, lec, state, fallback: str) -> None:
    """재생 실패/출석 미반영 텔레그램 알림. 설정이 꺼져 있으면 아무것도 하지 않는다."""
    from src.config import Config
    from src.notifier import telegram_notifier

    if not Config.should_notify("error"):
        return
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
            state.error or fallback,
        )


async def _run_post_play_pipeline(course, lec, page) -> None:
    """재생 완료 후 다운로드 → STT → 요약 → 텔레그램 파이프라인을 실행한다.

    Config 설정에 따라 각 단계를 선택적으로 실행한다.

    page: 영상 URL 추출에 사용할 전용 Playwright page. 재생에 쓴 page를 재사용하면
          오염된 SPA 상태에서 URL을 추출하게 되므로 호출자가 새 page를 넘긴다.
    """
    from pathlib import Path

    from src.config import Config
    from src.downloader.pipeline import DownloadUnsupportedError, run_download_from_config
    from src.notifier import telegram_notifier

    bot_token = Config.TELEGRAM_BOT_TOKEN or ""
    chat_id = Config.TELEGRAM_CHAT_ID or ""

    loop = asyncio.get_running_loop()

    # 재생 완료 텔레그램 알림
    if Config.should_notify("playback"):
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
            page=page,
            lecture_url=lec.full_url,
            lecture_title=lec.title,
            week_label=lec.week_label,
            course_name=course.long_name,
            on_stage=_on_stage,
        )

        # 음성이 없는 영상(샘플/플레이스홀더)은 STT가 빈 결과를 내므로 요약을 건너뛴다 — 오류가 아니다.
        if (result.get("stt") or {}).get("status") == "empty":
            logger.info("STT 결과 없음 — 요약 건너뜀: %s / %s (음성 미검출)", course.long_name, lec.title)

        # 요약 완료 시 텔레그램으로 요약 전송
        summary_result = result.get("summary") or {}
        if Config.should_notify("summary") and summary_result.get("status") == "completed":
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
        if Config.should_notify("error"):
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
        if Config.should_notify("error"):
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
    from backend.api.routes.player import (
        _mark_lecture_completed,
        _verification_metadata,
        _write_playback_log,
    )

    from src.player.background_player import play_lecture

    if not app_state.scraper:
        app_state.auto.error = "로그인 상태가 아닙니다."
        app_state.auto.enabled = False
        return

    app_state.auto.error = None

    # 강의 목록 갱신 — ensure_courses_loaded/refresh 등 다른 스크래핑과 직렬화한다.
    # (락 없이 실행하면 사이클 종료 후 브라우저 재시작이 진행 중인 fetch_all_details를
    #  끊어 일부 과목 상세가 None이 되는 레이스가 발생한다)
    try:
        async with scraper_lock:
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

    if Config.should_notify("deadline"):
        from src.notifier.deadline_checker import check_and_notify_deadlines

        loop = asyncio.get_running_loop()
        with suppress(Exception):
            await loop.run_in_executor(None, check_and_notify_deadlines, courses, details)

    # 미시청 강의 수집 — 반복 실패로 억제된 강의는 제외한다 (사이클 시작 시 1회 벌크 조회).
    from src import playback_ledger

    suppressed = playback_ledger.suppressed_urls()
    pending: list[tuple] = []
    for course, detail in zip(courses, details, strict=False):
        if detail is None:
            continue
        for lec in detail.all_video_lectures:
            if lec.needs_watch and lec.full_url not in suppressed:
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
                async with scraper_lock:
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

        # 강의마다 새 page를 쓴다 — 같은 탭을 재사용하면 init script/SPA 상태가 누적돼
        # 사이클당 첫 강의만 LMS 출석이 반영되던 문제가 재발한다.
        run_pipeline = False
        play_page = None
        try:
            play_page = await app_state.scraper.new_page()
            final_state = await play_lecture(
                play_page,
                lec.full_url,
                on_progress=_on_progress,
                debug=True,
                log_fn=log_buffer.append,
                verify_fn=_make_verify_fn(course, lec),
            )
            _on_progress(final_state)

            if final_state.cancelled:
                # 재생이 취소됨 → 이번 세션의 자동 루프는 멈추되, 지속 상태(DB)는 건드리지
                # 않는다. 자동 모드는 '자동 모드 중지'로만 완전히 꺼지고, 재로그인 시 재개된다.
                app_state.playback.status = "stopped"
                app_state.auto.enabled = False
                break
            elif final_state.error or final_state.verified is False:
                # 완료 처리하지 않고 후처리 파이프라인도 돌리지 않는다 (토큰 낭비 차단).
                app_state.playback.status = "error"
                app_state.playback.log_path = _write_playback_log(
                    lec.title, lec.full_url, final_state.error or "출석 미반영", log_buffer
                )
                if final_state.verified is False:
                    # 재스크래핑으로 출석 미반영이 확정됨 — 재시도해도 같을 가능성이 높으므로
                    # 억제 카운트에 반영한다.
                    _log_attendance_not_recorded(course, lec, final_state)
                    await _notify_playback_failure(course, lec, final_state, "LMS에 출석이 반영되지 않았습니다.")
                    await _record_ledger_attempt(course, lec, playback_ledger.RESULT_FAILED, final_state.error)
                else:
                    # 브라우저 크래시·타임아웃·ErrAlreadyInView·영상 URL 추출 실패 등
                    # 일시적 오류. 다음 사이클에 재시도돼야 하므로 억제 카운트에 넣지 않는다.
                    _log_playback_error(course, lec, final_state)
                    await _notify_playback_failure(course, lec, final_state, "재생 중 오류가 발생했습니다.")
                    await _record_ledger_attempt(course, lec, playback_ledger.RESULT_ERROR, final_state.error)
            elif final_state.ended:
                app_state.playback.status = "completed"
                updated = _mark_lecture_completed(course.id, lec.full_url)
                if not updated:
                    app_state.playback.refresh_recommended = True
                app_state.auto.processed_count += 1
                # 완료 + 출석 검증 결과를 이벤트 로그에 남긴다 (성공 케이스도 추적 가능하도록).
                ratio = final_state.lms_progress_ratio
                from src import event_log

                with suppress(Exception):
                    event_log.record_event(
                        event_type="player",
                        action="play_complete",
                        status="success",
                        actor_user_id=app_state.user_id or None,
                        target_type="lecture",
                        course_id=course.id,
                        course_name=course.long_name,
                        lecture_title=lec.title,
                        lecture_url=lec.full_url,
                        week_label=lec.week_label,
                        message="자동 재생 완료",
                        metadata=_verification_metadata(final_state),
                    )
                logger.info(
                    "재생 완료: %s / %s (검증=%s, 진도보고=%s, LMS진도=%s)",
                    course.long_name,
                    lec.title,
                    final_state.verified,
                    final_state.progress_reported,
                    f"{ratio * 100:.0f}%" if ratio is not None else "확인불가",
                )
                # verified=True면 원장 행을 지워 이력을 초기화하고, 검증 불가(None)는
                # unverified로 카운트해 상시화되면 결국 억제되게 한다.
                await _record_ledger_attempt(
                    course,
                    lec,
                    playback_ledger.RESULT_VERIFIED if final_state.verified else playback_ledger.RESULT_UNVERIFIED,
                )
                # verified is None(검증 불가)에서도 파이프라인을 돌린다 — 검증 인프라 장애로
                # 정상 재생의 요약이 영구 누락되는 것을 막기 위함. 무한 반복은 억제 원장이 막는다.
                run_pipeline = True
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
            if play_page is not None:
                with suppress(Exception):
                    await app_state.scraper.close_page(play_page)

        # 후처리 파이프라인은 재생 page를 닫은 뒤 전용 page에서 실행한다.
        if run_pipeline:
            pipeline_page = None
            try:
                pipeline_page = await app_state.scraper.new_page()
                await _run_post_play_pipeline(course, lec, pipeline_page)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                app_state.auto.error = f"파이프라인 오류: {e}"
            finally:
                if pipeline_page is not None:
                    with suppress(Exception):
                        await app_state.scraper.close_page(pipeline_page)

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
                    async with scraper_lock:
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
    from src import playback_ledger

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
        "suppressed_count": playback_ledger.suppressed_count(),
    }


class SuppressionReset(BaseModel):
    course_id: str | None = None
    lecture_url: str | None = None


@router.get("/suppressions")
async def list_suppressions():
    """반복 실패로 자동 재시도에서 제외된 강의 목록."""
    require_auth()
    from src import playback_ledger

    return {"suppressions": playback_ledger.list_suppressed()}


@router.delete("/suppressions")
async def reset_suppressions(req: SuppressionReset | None = None):
    """억제를 해제한다. body가 비어 있으면 전체 해제."""
    require_auth()
    from src import playback_ledger

    course_id = req.course_id if req else None
    lecture_url = req.lecture_url if req else None
    # 부분 인자를 조용히 전체 삭제로 처리하지 않는다.
    if lecture_url and not course_id:
        raise HTTPException(status_code=422, detail="course_id 없이 lecture_url만 지정할 수 없습니다.")
    return {"reset": playback_ledger.reset(course_id, lecture_url)}


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
