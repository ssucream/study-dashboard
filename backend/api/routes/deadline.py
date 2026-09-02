"""마감 임박 알림 API."""

import asyncio

from backend.api.auth_dep import require_auth
from backend.api.routes.courses import ensure_courses_loaded
from backend.api.state import app_state
from fastapi import APIRouter

router = APIRouter()


@router.post("/check")
async def check_deadlines():
    """미완료 과제·퀴즈 중 마감 임박 항목을 조회하고, 텔레그램이 설정된 경우 알림을 전송한다.

    로그인 직후 프론트가 자동 호출하므로, 과목 목록이 아직 로드되지 않았으면
    여기서 직접 로드한다(대시보드 통계 로드와 락으로 직렬화됨).
    """
    require_auth()
    await ensure_courses_loaded()

    from src.config import Config
    from src.notifier.deadline_checker import find_approaching_deadlines

    loop = asyncio.get_running_loop()
    # 표시는 전송 시점 설정과 무관하게 항상 7일 이내 전체.
    items = await loop.run_in_executor(
        None, find_approaching_deadlines, app_state.courses, app_state.details
    )

    sent_count = 0
    telegram_enabled = Config.should_notify("deadline")
    if telegram_enabled:
        from src.notifier.deadline_checker import check_and_notify_deadlines

        sent_count = await loop.run_in_executor(
            None,
            check_and_notify_deadlines,
            app_state.courses,
            app_state.details,
        )

    return {
        "found": len(items),
        "sent": sent_count,
        "telegram_enabled": telegram_enabled,
        "items": [
            {
                "course": item.course.long_name,
                "title": item.lecture.title,
                "type": item.type_label,
                "end_date": item.lecture.end_date,
                "remaining_hours": round(item.remaining_hours, 1),
            }
            for item in items
        ],
    }
