import logging
import os
from contextlib import asynccontextmanager

from backend.api.routes import auth, auto, courses, deadline, logs, player, settings, summaries, tasks
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from src import db
    from src.config import Config

    try:
        db.init()
    except Exception as e:
        logger.critical("DB 초기화 실패 — 앱을 시작할 수 없습니다: %s", e)
        raise RuntimeError(f"DB 초기화 실패: {e}") from e

    try:
        Config.load()
    except Exception as e:
        logger.critical("설정 로드 실패 — 앱을 시작할 수 없습니다: %s", e)
        raise RuntimeError(f"설정 로드 실패: {e}") from e

    from backend.api.task_manager import task_manager

    task_manager.purge_old(days=7)
    task_manager.load_from_db(days=7)

    yield
    from backend.api.state import app_state

    if app_state.scraper:
        await app_state.scraper.close()


app = FastAPI(title="Study Helper API", version="1.0.0", lifespan=lifespan)

# 기본값: 로컬 Docker 서비스 용도. 외부 노출 시 CORS_ALLOWED_ORIGINS 환경변수로 origin 목록을 콤마 구분 지정.
_raw_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS", "http://localhost,http://localhost:80,http://localhost:443,http://127.0.0.1"
)
_CORS_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(courses.router, prefix="/api/courses", tags=["courses"])
app.include_router(player.router, prefix="/api/player", tags=["player"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(auto.router, prefix="/api/auto", tags=["auto"])
app.include_router(summaries.router, prefix="/api/summaries", tags=["summaries"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(deadline.router, prefix="/api/deadline", tags=["deadline"])


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version_check():
    import asyncio

    from src.config import APP_VERSION
    from src.updater import check_update

    loop = asyncio.get_running_loop()
    latest = await loop.run_in_executor(None, check_update, APP_VERSION)
    return {"current": APP_VERSION, "update_available": latest is not None, "latest": latest}


# 로컬 dev: frontend/ 정적 파일 서빙 (Docker에서는 nginx가 담당하므로 dir 없으면 스킵)
from pathlib import Path  # noqa: E402

_frontend_dir = Path(__file__).parent.parent / "frontend"
if _frontend_dir.is_dir():
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
