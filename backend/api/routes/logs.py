"""행위 로그 조회 API."""

from backend.api.auth_dep import require_auth
from fastapi import APIRouter, Query

from src.event_log import list_events

router = APIRouter()


@router.get("")
async def get_logs(
    event_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    require_auth()
    return {"events": list_events(event_type=event_type, status=status, limit=limit)}
