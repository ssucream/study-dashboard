import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from backend.api.routes import auth, auto, courses, deadline, logs, player, quiz, settings, summaries, tasks
from backend.api.routes import ws as ws_route
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 앱 모듈(backend.*, src.*)의 INFO 로그가 uvicorn 로그와 함께 stdout에 보이도록 한다.
# uvicorn은 자체 로거만 설정하고 root logger는 건드리지 않아, 이게 없으면 app의
# logger.info(...)가 출력되지 않는다.
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if not logging.getLogger().handlers:
    logging.basicConfig(level=_LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
else:
    logging.getLogger().setLevel(_LOG_LEVEL)

logger = logging.getLogger(__name__)


async def _resume_auto_from_saved_session() -> None:
    """백엔드 부팅 시, 저장된 세션 쿠키로 로그인 없이 자동 모드를 재개해본다.

    학번/비밀번호는 절대 DB에 저장하지 않으므로 자격증명 기반 자동 로그인은 불가능하다.
    대신 마지막 로그인 시 저장해둔 암호화된 세션 쿠키(Playwright storage_state)로
    브라우저 컨텍스트를 복원해, 컨테이너 재시작으로 스케줄이 끊기지 않게 한다.
    쿠키가 이미 만료됐으면 조용히 포기하고(텔레그램 알림만) 기존처럼 수동 로그인을 기다린다.
    """
    from backend.api.routes.auto import resume_persisted_auto
    from backend.api.state import app_state

    from src import event_log
    from src.config import Config

    if Config.AUTO_ENABLED != "true":
        return

    session = None
    with suppress(Exception):
        session = Config.load_session_state()
    if not session or not session[1]:
        return

    resume_user_id, resume_storage_state = session

    from src.scraper.course_scraper import CourseScraper

    scraper = CourseScraper(username="", password="")
    try:
        await scraper.start(storage_state=resume_storage_state)
        await scraper.fetch_courses()
    except Exception as e:
        logger.info("저장된 세션으로 자동 모드 재개 실패 — 수동 로그인이 필요합니다: %s", e)
        with suppress(Exception):
            await scraper.close()
        if Config.should_notify("error"):
            from src.notifier import telegram_notifier

            loop = asyncio.get_running_loop()
            with suppress(Exception):
                await loop.run_in_executor(
                    None,
                    telegram_notifier.notify_session_resume_failed,
                    Config.TELEGRAM_BOT_TOKEN,
                    Config.TELEGRAM_CHAT_ID,
                )
        return

    app_state.scraper = scraper
    app_state.user_id = resume_user_id
    logger.info("저장된 세션으로 백엔드 시작 시 자동 모드 재개 (user=%s)", event_log.mask_user_id(resume_user_id))
    with suppress(Exception):
        event_log.record_event(
            event_type="auto",
            action="resume",
            status="success",
            actor_user_id=event_log.mask_user_id(resume_user_id),
            message="백엔드 재시작 시 저장된 세션으로 자동 모드 자동 재개",
        )
    resume_persisted_auto()


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

    from backend.api.state import app_state

    await _resume_auto_from_saved_session()

    yield

    # 실행 중인 task(다운로드/재생/자동모드)를 취소해 부분 상태로 방치되지 않게 한다.
    # task_manager.cancel()의 finally에서 _persist_task()가 최종 상태를 DB에 남긴다.
    running = [t for t in task_manager.list() if t.task and not t.task.done()]
    if running:
        await asyncio.gather(*(task_manager.cancel(t.id) for t in running))

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
app.include_router(quiz.router, prefix="/api/quiz", tags=["quiz"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(logs.router, prefix="/api/logs", tags=["logs"])
app.include_router(deadline.router, prefix="/api/deadline", tags=["deadline"])
app.include_router(ws_route.router)


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
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
