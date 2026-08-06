"""강의 요약 조회 API."""

from backend.api.state import app_state
from backend.api.summary_store import list_summaries, read_summary
from fastapi import APIRouter, HTTPException

router = APIRouter()


def _require_auth() -> None:
    if not app_state.scraper:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")


@router.get("")
async def get_summaries_list():
    _require_auth()
    return {"summaries": list_summaries()}


@router.get("/{summary_id}/download")
async def download_summary(summary_id: str):
    """요약 파일을 다운로드한다."""
    from pathlib import Path

    from backend.api.summary_store import _decode_summary_id
    from fastapi.responses import FileResponse

    _require_auth()
    try:
        path = Path(_decode_summary_id(summary_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="요약 파일을 찾을 수 없습니다.")
    return FileResponse(path=path, filename=path.name, media_type="text/plain; charset=utf-8")


@router.get("/{summary_id}")
async def get_summary(summary_id: str):
    _require_auth()
    try:
        return read_summary(summary_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
