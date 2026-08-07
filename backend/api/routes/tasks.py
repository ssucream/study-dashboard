"""공통 백그라운드 태스크 상태 API."""

from backend.api.state import app_state
from backend.api.task_manager import ManagedTask, task_manager
from backend.api.validators import validate_lecture_url
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from src import event_log
from src.config import Config

router = APIRouter()


async def _notify_summary_complete(
    course_name: str,
    week_label: str,
    lecture_title: str,
    summary_result: dict,
    download_files: list,
) -> None:
    """요약 완료 시 텔레그램으로 요약 문서 전송. 설정 미완성이면 무시."""
    import asyncio
    from contextlib import suppress
    from pathlib import Path

    from src.notifier import telegram_notifier

    if not (Config.TELEGRAM_ENABLED == "true" and Config.TELEGRAM_BOT_TOKEN and Config.TELEGRAM_CHAT_ID):
        return

    summary_path_str = summary_result.get("summary_path", "")
    if not summary_path_str:
        return
    summary_path = Path(summary_path_str)
    if not summary_path.is_file():
        return

    summary_text = summary_path.read_text(encoding="utf-8")
    auto_delete: list[Path] = []
    if Config.TELEGRAM_AUTO_DELETE == "true":
        for f in download_files:
            if f.get("deleted") != "true":
                p = Path(f["path"])
                if p.exists():
                    auto_delete.append(p)

    with suppress(Exception):
        await asyncio.get_running_loop().run_in_executor(
            None,
            telegram_notifier.notify_summary_complete,
            Config.TELEGRAM_BOT_TOKEN,
            Config.TELEGRAM_CHAT_ID,
            course_name,
            week_label,
            lecture_title,
            summary_text,
            summary_path,
            auto_delete or None,
        )


def _require_auth() -> None:
    if not app_state.scraper:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")


class DownloadTaskRequest(BaseModel):
    course_id: str
    lecture_url: str
    lecture_title: str
    week_label: str = ""

    _validate_lecture_url = field_validator("lecture_url")(validate_lecture_url)


def _find_course(course_id: str):
    return next((course for course in app_state.courses if course.id == course_id), None)


@router.post("/download")
async def start_download(req: DownloadTaskRequest):
    _require_auth()
    if Config.DOWNLOAD_ENABLED != "true":
        raise HTTPException(status_code=409, detail="설정에서 영상 다운로드를 먼저 활성화하세요.")
    if app_state.is_playing:
        raise HTTPException(status_code=409, detail="재생 중에는 다운로드를 시작할 수 없습니다.")
    if app_state.auto.enabled:
        raise HTTPException(status_code=409, detail="자동 모드 실행 중에는 다운로드를 시작할 수 없습니다.")
    if any(t.kind == "download" and t.status in {"queued", "running"} for t in task_manager.list()):
        raise HTTPException(status_code=409, detail="다른 다운로드가 진행 중입니다. 완료 후 다시 시도하세요.")

    course = _find_course(req.course_id)
    if not course:
        raise HTTPException(status_code=404, detail="과목을 찾을 수 없습니다.")

    from src.downloader.pipeline import DownloadUnsupportedError, PipelineStageError, download_lecture_media

    async def run(managed: ManagedTask):
        def on_stage(stage: str, message: str, progress_pct: float | None = None) -> None:
            managed.update(stage=stage, message=message, progress_pct=progress_pct)

        try:
            result = await download_lecture_media(
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
                ai_api_key=Config.GOOGLE_API_KEY,
                ai_model=Config.GEMINI_MODEL,
                summary_prompt_template=Config.get_summary_prompt_template(),
                summary_prompt_extra=Config.SUMMARY_PROMPT_EXTRA,
                delete_text_after_summary=Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE == "true",
                on_stage=on_stage,
            )
            stt_result = result.get("stt") or {}
            if stt_result.get("status") == "completed":
                event_log.record_event(
                    event_type="stt",
                    action="transcribe_complete",
                    status="success",
                    actor_user_id=app_state.user_id or None,
                    target_type="lecture",
                    course_id=req.course_id,
                    course_name=course.long_name,
                    lecture_title=req.lecture_title,
                    lecture_url=req.lecture_url,
                    week_label=req.week_label,
                    message="STT 변환이 완료되었습니다.",
                    metadata={"task_id": managed.id, "stt": stt_result},
                )
            summary_result = result.get("summary") or {}
            if summary_result.get("status") == "completed":
                event_log.record_event(
                    event_type="summary",
                    action="summary_complete",
                    status="success",
                    actor_user_id=app_state.user_id or None,
                    target_type="lecture",
                    course_id=req.course_id,
                    course_name=course.long_name,
                    lecture_title=req.lecture_title,
                    lecture_url=req.lecture_url,
                    week_label=req.week_label,
                    message="AI 요약이 완료되었습니다.",
                    metadata={"task_id": managed.id, "summary": summary_result},
                )
                await _notify_summary_complete(
                    course_name=course.long_name,
                    week_label=req.week_label,
                    lecture_title=req.lecture_title,
                    summary_result=summary_result,
                    download_files=result.get("files", []),
                )
            event_log.record_event(
                event_type="download",
                action="download_complete",
                status="success",
                actor_user_id=app_state.user_id or None,
                target_type="lecture",
                course_id=req.course_id,
                course_name=course.long_name,
                lecture_title=req.lecture_title,
                lecture_url=req.lecture_url,
                week_label=req.week_label,
                message="다운로드가 완료되었습니다.",
                metadata={"task_id": managed.id, "result": result},
            )
            return result
        except DownloadUnsupportedError as e:
            managed.update(status="failed", stage="unsupported", error=str(e), message=str(e))
            event_log.record_event(
                event_type="download",
                action="download_unsupported",
                status="failed",
                actor_user_id=app_state.user_id or None,
                target_type="lecture",
                course_id=req.course_id,
                course_name=course.long_name,
                lecture_title=req.lecture_title,
                lecture_url=req.lecture_url,
                week_label=req.week_label,
                error_code="unsupported",
                error_message=str(e),
                metadata={"task_id": managed.id},
            )
            return {}
        except Exception as e:
            if isinstance(e, PipelineStageError):
                managed.update(result=e.partial_result)
                e = e.original
            if managed.stage == "transcribing":
                event_log.record_event(
                    event_type="stt",
                    action="transcribe_failed",
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
                    metadata={"task_id": managed.id},
                )
            if managed.stage == "summarizing":
                event_log.record_event(
                    event_type="summary",
                    action="summary_failed",
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
                    metadata={"task_id": managed.id},
                )
            event_log.record_event(
                event_type="download",
                action="download_failed",
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
                metadata={"task_id": managed.id},
            )
            raise

    managed = task_manager.create(
        "download",
        run,
        metadata={
            "course_id": req.course_id,
            "course_name": course.long_name,
            "lecture_title": req.lecture_title,
            "week_label": req.week_label,
            "download_rule": Config.get_download_rule(),
            "stt_enabled": Config.STT_ENABLED,
            "stt_delete_audio_after_transcribe": Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE,
            "ai_enabled": Config.AI_ENABLED,
            "summary_delete_text_after_summarize": Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE,
        },
    )
    event_log.record_event(
        event_type="download",
        action="download_start",
        status="started",
        actor_user_id=app_state.user_id or None,
        target_type="lecture",
        course_id=req.course_id,
        course_name=course.long_name,
        lecture_title=req.lecture_title,
        lecture_url=req.lecture_url,
        week_label=req.week_label,
        message="다운로드를 시작했습니다.",
        metadata={
            "task_id": managed.id,
            "download_rule": Config.get_download_rule(),
            "stt_enabled": Config.STT_ENABLED,
        },
    )
    return {"started": True, "task_id": managed.id}


@router.get("")
async def list_tasks():
    _require_auth()
    return {"tasks": [task.to_dict() for task in task_manager.list()]}


@router.get("/{task_id}")
async def get_task(task_id: str):
    _require_auth()
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return task.to_dict()


@router.get("/{task_id}/stt")
async def get_stt_text(task_id: str):
    _require_auth()
    from pathlib import Path

    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    stt = task.result.get("stt") or {}
    if stt.get("status") != "completed":
        raise HTTPException(status_code=404, detail="STT 결과가 없습니다.")
    txt_path_str = stt.get("txt_path")
    if not txt_path_str:
        raise HTTPException(status_code=404, detail="STT 파일 경로가 없습니다.")
    txt_path = Path(txt_path_str)
    if not txt_path.is_file():
        raise HTTPException(status_code=404, detail="STT 텍스트 파일을 찾을 수 없습니다.")
    content = txt_path.read_text(encoding="utf-8")
    return {
        "task_id": task_id,
        "lecture_title": task.metadata.get("lecture_title", ""),
        "content": content,
        "model": stt.get("model", ""),
        "language": stt.get("language", ""),
    }


@router.get("/{task_id}/stt/download")
async def download_stt_file(task_id: str):
    """STT 텍스트 파일을 다운로드한다."""
    from pathlib import Path

    from fastapi.responses import FileResponse

    _require_auth()
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    stt = (task.result or {}).get("stt") or {}
    txt_path_str = stt.get("txt_path")
    if not txt_path_str:
        raise HTTPException(status_code=404, detail="STT 파일이 없습니다.")
    txt_path = Path(txt_path_str)
    if not txt_path.is_file():
        raise HTTPException(status_code=404, detail="STT 파일을 찾을 수 없습니다.")
    return FileResponse(path=txt_path, filename=txt_path.name, media_type="text/plain; charset=utf-8")


@router.post("/{task_id}/summarize")
async def start_summarize(task_id: str):
    _require_auth()
    import asyncio
    from functools import partial
    from pathlib import Path

    if Config.AI_ENABLED != "true":
        raise HTTPException(status_code=409, detail="설정에서 AI 요약을 먼저 활성화하세요.")
    if not Config.GOOGLE_API_KEY:
        raise HTTPException(status_code=409, detail="Gemini API 키가 설정되어 있지 않습니다.")
    if not Config.GEMINI_MODEL:
        raise HTTPException(status_code=409, detail="Gemini 모델이 설정되어 있지 않습니다.")

    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")

    stt = (task.result or {}).get("stt") or {}
    if stt.get("status") != "completed":
        raise HTTPException(status_code=409, detail="STT 결과가 없습니다.")

    txt_path_str = stt.get("txt_path", "")
    txt_path = Path(txt_path_str) if txt_path_str else None
    if not txt_path or not txt_path.is_file():
        raise HTTPException(status_code=404, detail="STT 텍스트 파일을 찾을 수 없습니다.")

    course_name = task.metadata.get("course_name", "")
    lecture_title = task.metadata.get("lecture_title", "")
    week_label = task.metadata.get("week_label", "")

    from src.downloader.pipeline import build_download_paths

    try:
        _base, _mp4, _mp3, _txt, summary_out_path = build_download_paths(
            download_dir=Config.get_download_dir(),
            course_name=course_name,
            week_label=week_label,
            lecture_title=lecture_title,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    async def run(managed: ManagedTask):

        from backend.api.summary_store import encode_summary_id

        from src.summarizer.summarizer import summarize

        managed.update(stage="summarizing", message="AI 요약 중입니다.")
        loop = asyncio.get_running_loop()
        summary_path = await loop.run_in_executor(
            None,
            partial(
                summarize,
                txt_path,
                agent=Config.AI_AGENT or "gemini",
                api_key=Config.GOOGLE_API_KEY,
                model=Config.GEMINI_MODEL,
                prompt_template=Config.get_summary_prompt_template(),
                extra_prompt=Config.SUMMARY_PROMPT_EXTRA or "",
                course_name=course_name,
                output_path=summary_out_path,
            ),
        )
        summary_id = encode_summary_id(summary_path)
        event_log.record_event(
            event_type="summary",
            action="summary_complete",
            status="success",
            actor_user_id=app_state.user_id or None,
            target_type="lecture",
            course_name=course_name,
            lecture_title=lecture_title,
            week_label=week_label,
            message="AI 요약이 완료되었습니다.",
            metadata={"task_id": managed.id, "source_task_id": task_id},
        )
        return {
            "status": "completed",
            "summary_path": str(summary_path),
            "summary_id": summary_id,
            "txt_path": txt_path_str,
            "agent": Config.AI_AGENT or "gemini",
            "model": Config.GEMINI_MODEL,
        }

    managed = task_manager.create(
        "summarize",
        run,
        metadata={
            "source_task_id": task_id,
            "course_name": course_name,
            "lecture_title": lecture_title,
            "week_label": week_label,
        },
    )
    return {"started": True, "task_id": managed.id}


class SummarizeFromFileRequest(BaseModel):
    course_id: str
    lecture_title: str
    week_label: str = ""


@router.post("/summarize-from-file")
async def start_summarize_from_file(req: SummarizeFromFileRequest):
    _require_auth()
    import asyncio
    from functools import partial
    from pathlib import Path

    if Config.AI_ENABLED != "true":
        raise HTTPException(status_code=409, detail="설정에서 AI 요약을 먼저 활성화하세요.")
    if not Config.GOOGLE_API_KEY:
        raise HTTPException(status_code=409, detail="Gemini API 키가 설정되어 있지 않습니다.")
    if not Config.GEMINI_MODEL:
        raise HTTPException(status_code=409, detail="Gemini 모델이 설정되어 있지 않습니다.")

    course = _find_course(req.course_id)
    if not course:
        raise HTTPException(status_code=404, detail="과목을 찾을 수 없습니다.")

    from src.downloader.pipeline import build_download_paths

    try:
        _base, mp4_path, mp3_path, txt_path, summary_out_path = build_download_paths(
            download_dir=Config.get_download_dir(),
            course_name=course.long_name,
            week_label=req.week_label,
            lecture_title=req.lecture_title,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    audio_path: Path | None = None
    if mp3_path.is_file():
        audio_path = mp3_path
    elif mp4_path.is_file():
        audio_path = mp4_path

    if not txt_path.is_file() and not audio_path:
        raise HTTPException(status_code=404, detail="다운로드된 파일을 찾을 수 없습니다. 먼저 다운로드를 진행해주세요.")

    course_name = course.long_name
    lecture_title = req.lecture_title
    week_label = req.week_label

    async def run(managed: ManagedTask):

        from backend.api.summary_store import encode_summary_id

        from src.summarizer.summarizer import summarize

        current_txt = txt_path
        current_audio = audio_path

        # STT — txt 파일이 없을 경우에만 실행
        if not current_txt.is_file() and current_audio:
            managed.update(
                stage="stt_loading",
                message=f"Whisper {Config.WHISPER_MODEL or 'base'} 모델을 로딩하는 중입니다.",
                progress_pct=20,
            )
            from src.stt.transcriber import transcribe

            loop = asyncio.get_running_loop()

            def _on_model_loaded() -> None:
                loop.call_soon_threadsafe(
                    lambda: managed.update(stage="transcribing", message="STT 변환 중입니다.", progress_pct=50)
                )

            current_txt = await loop.run_in_executor(
                None,
                partial(
                    transcribe,
                    current_audio,
                    model_size=Config.WHISPER_MODEL or "base",
                    language=Config.STT_LANGUAGE or "",
                    on_model_loaded=_on_model_loaded,
                    output_path=txt_path,
                ),
            )
            event_log.record_event(
                event_type="stt",
                action="transcribe_complete",
                status="success",
                actor_user_id=app_state.user_id or None,
                target_type="lecture",
                course_name=course_name,
                lecture_title=lecture_title,
                week_label=week_label,
                message="STT 변환이 완료되었습니다.",
                metadata={"task_id": managed.id},
            )
            if Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE == "true" and current_audio:
                current_audio.unlink(missing_ok=True)

        if not current_txt.is_file():
            raise RuntimeError("STT 텍스트 파일을 찾을 수 없습니다.")

        managed.update(stage="summarizing", message="AI 요약 중입니다.", progress_pct=80)
        loop = asyncio.get_running_loop()
        summary_path = await loop.run_in_executor(
            None,
            partial(
                summarize,
                current_txt,
                agent=Config.AI_AGENT or "gemini",
                api_key=Config.GOOGLE_API_KEY,
                model=Config.GEMINI_MODEL,
                prompt_template=Config.get_summary_prompt_template(),
                extra_prompt=Config.SUMMARY_PROMPT_EXTRA or "",
                course_name=course_name,
                output_path=summary_out_path,
            ),
        )
        summary_id = encode_summary_id(summary_path)
        event_log.record_event(
            event_type="summary",
            action="summary_complete",
            status="success",
            actor_user_id=app_state.user_id or None,
            target_type="lecture",
            course_name=course_name,
            lecture_title=lecture_title,
            week_label=week_label,
            message="AI 요약이 완료되었습니다.",
            metadata={"task_id": managed.id},
        )
        if Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE == "true":
            current_txt.unlink(missing_ok=True)

        return {
            "status": "completed",
            "summary_path": str(summary_path),
            "summary_id": summary_id,
            "agent": Config.AI_AGENT or "gemini",
            "model": Config.GEMINI_MODEL,
        }

    managed = task_manager.create(
        "summarize_from_file",
        run,
        metadata={
            "course_id": req.course_id,
            "course_name": course_name,
            "lecture_title": lecture_title,
            "week_label": week_label,
        },
    )
    return {"started": True, "task_id": managed.id}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str):
    _require_auth()
    task_before = task_manager.get(task_id)
    cancelled = await task_manager.cancel(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    task = task_manager.get(task_id)
    if task_before and task_before.kind == "download":
        event_log.record_event(
            event_type="download",
            action="download_cancel",
            status="cancelled",
            actor_user_id=app_state.user_id or None,
            course_id=task_before.metadata.get("course_id"),
            course_name=task_before.metadata.get("course_name"),
            lecture_title=task_before.metadata.get("lecture_title"),
            week_label=task_before.metadata.get("week_label"),
            message="다운로드 취소 요청",
            metadata={"task_id": task_id},
        )
    return task.to_dict() if task else {"cancelled": True}
