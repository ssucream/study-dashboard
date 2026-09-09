"""강의 요약 조회 API."""

from pathlib import Path

from backend.api.auth_dep import require_auth
from backend.api.summary_store import (
    list_summaries,
    read_summary,
    read_transcript,
    transcript_path_from_id,
)
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("")
async def get_summaries_list():
    require_auth()
    return {"summaries": list_summaries()}


@router.get("/transcript/{transcript_id}/download")
async def download_transcript(transcript_id: str):
    """STT 원문 텍스트 파일을 다운로드한다."""
    require_auth()
    try:
        path = transcript_path_from_id(transcript_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return FileResponse(path=path, filename=path.name, media_type="text/plain; charset=utf-8")


@router.get("/transcript/{transcript_id}")
async def get_transcript(transcript_id: str):
    """STT 원문(txt) 내용을 반환한다. 요약 시 원본이 삭제됐으면 404."""
    require_auth()
    try:
        return read_transcript(transcript_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{summary_id}/download")
async def download_summary(summary_id: str):
    """요약 파일을 다운로드한다."""
    from backend.api.summary_store import _decode_summary_id

    require_auth()
    try:
        path = Path(_decode_summary_id(summary_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not path.is_file():
        raise HTTPException(status_code=404, detail="요약 파일을 찾을 수 없습니다.")
    return FileResponse(path=path, filename=path.name, media_type="text/plain; charset=utf-8")


@router.get("/{summary_id}")
async def get_summary(summary_id: str):
    require_auth()
    try:
        return read_summary(summary_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
