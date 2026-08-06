"""WebSocket 상태 스트리밍 — 재생·자동모드 상태를 2초 주기로 push한다."""

import asyncio
import json

from backend.api.state import app_state
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    if not app_state.scraper:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        while True:
            pb = app_state.playback
            auto = app_state.auto
            await websocket.send_text(
                json.dumps(
                    {
                        "player": {
                            "is_playing": app_state.is_playing,
                            "status": pb.status,
                            "course_name": app_state.current_course_name or None,
                            "course_id": app_state.current_course_id or None,
                            "lecture_title": app_state.current_lecture_title or None,
                            "lecture_url": app_state.current_lecture_url or None,
                            "week_label": app_state.current_week_label or None,
                            "current": pb.current,
                            "duration": pb.duration,
                            "progress_pct": pb.progress_pct,
                            "ended": pb.ended,
                            "error": pb.error,
                            "log_path": pb.log_path,
                            "refresh_recommended": pb.refresh_recommended,
                            "task_id": app_state.play_task_id,
                            "auto_download_task_id": app_state.auto_download_task_id,
                        },
                        "auto": {
                            "enabled": auto.enabled,
                            "schedule_hours": auto.schedule_hours,
                            "processed_count": auto.processed_count,
                            "current_course": auto.current_course,
                            "current_lecture": auto.current_lecture,
                            "next_run_at": auto.next_run_at,
                            "error": auto.error,
                            "pipeline_stage": auto.pipeline_stage,
                        },
                    }
                )
            )
            await asyncio.sleep(2)
    except (WebSocketDisconnect, Exception):
        pass
