from backend.api.auth_dep import require_auth
from backend.api.state import app_state
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src import db, event_log
from src.config import Config, normalize_download_rule
from src.crypto import encrypt
from src.summarizer.summarizer import DEFAULT_SUMMARY_PROMPT, get_model_catalog

router = APIRouter()

_SENSITIVE = {"GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "TELEGRAM_BOT_TOKEN"}


@router.get("/ai-models")
async def get_ai_models():
    """2026-08 기준 카탈로그와 OpenRouter 실시간 사용 가능 모델을 반환한다."""
    require_auth()
    import asyncio

    providers = {}
    for agent in ("gemini", "openai"):
        models, source = get_model_catalog(agent)
        providers[agent] = {"models": models, "source": source}
    models, source = await asyncio.to_thread(get_model_catalog, "openrouter", Config.OPENROUTER_API_KEY or "")
    providers["openrouter"] = {"models": models, "source": source}
    return {"as_of": "2026-08", "providers": providers}


@router.get("")
async def get_settings():
    require_auth()
    return {
        "DOWNLOAD_ENABLED": Config.DOWNLOAD_ENABLED,
        "DOWNLOAD_DIR": Config.get_download_dir(),
        "DOWNLOAD_RULE": Config.get_download_rule(),
        "AUTO_DOWNLOAD_AFTER_PLAY": Config.AUTO_DOWNLOAD_AFTER_PLAY,
        "STT_ENABLED": Config.STT_ENABLED,
        "STT_LANGUAGE": Config.STT_LANGUAGE,
        "STT_DELETE_AUDIO_AFTER_TRANSCRIBE": Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE,
        "WHISPER_MODEL": Config.WHISPER_MODEL,
        "AI_ENABLED": Config.AI_ENABLED,
        "AI_AGENT": Config.AI_AGENT,
        "GEMINI_MODEL": Config.GEMINI_MODEL,
        "HAS_GOOGLE_API_KEY": bool(Config.GOOGLE_API_KEY),
        "OPENAI_MODEL": Config.OPENAI_MODEL,
        "HAS_OPENAI_API_KEY": bool(Config.OPENAI_API_KEY),
        "OPENROUTER_MODEL": Config.OPENROUTER_MODEL,
        "HAS_OPENROUTER_API_KEY": bool(Config.OPENROUTER_API_KEY),
        "SUMMARY_PROMPT_TEMPLATE": Config.get_summary_prompt_template(),
        "SUMMARY_PROMPT_DEFAULT": DEFAULT_SUMMARY_PROMPT,
        "SUMMARY_PROMPT_EXTRA": Config.SUMMARY_PROMPT_EXTRA,
        "SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE": Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE,
        "TELEGRAM_ENABLED": Config.TELEGRAM_ENABLED,
        "TELEGRAM_CHAT_ID": Config.TELEGRAM_CHAT_ID,
        "TELEGRAM_AUTO_DELETE": Config.TELEGRAM_AUTO_DELETE,
        "HAS_TELEGRAM_BOT_TOKEN": bool(Config.TELEGRAM_BOT_TOKEN),
    }


class SettingsUpdate(BaseModel):
    DOWNLOAD_ENABLED: str | None = None
    DOWNLOAD_RULE: str | None = None
    AUTO_DOWNLOAD_AFTER_PLAY: str | None = None
    STT_ENABLED: str | None = None
    STT_LANGUAGE: str | None = None
    STT_DELETE_AUDIO_AFTER_TRANSCRIBE: str | None = None
    WHISPER_MODEL: str | None = None
    AI_ENABLED: str | None = None
    AI_AGENT: str | None = None
    GEMINI_MODEL: str | None = None
    GOOGLE_API_KEY: str | None = None
    OPENAI_MODEL: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENROUTER_MODEL: str | None = None
    OPENROUTER_API_KEY: str | None = None
    SUMMARY_PROMPT_TEMPLATE: str | None = None
    SUMMARY_PROMPT_EXTRA: str | None = None
    SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE: str | None = None
    TELEGRAM_ENABLED: str | None = None
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    TELEGRAM_AUTO_DELETE: str | None = None


@router.post("/telegram/test")
async def test_telegram():
    require_auth()
    import asyncio

    from src.notifier.telegram_notifier import verify_bot

    token = Config.TELEGRAM_BOT_TOKEN or ""
    chat_id = Config.TELEGRAM_CHAT_ID or ""
    if not token or not chat_id:
        raise HTTPException(status_code=409, detail="텔레그램 봇 토큰과 Chat ID를 먼저 저장하세요.")

    loop = asyncio.get_running_loop()
    ok, error_msg = await loop.run_in_executor(None, verify_bot, token, chat_id)
    if not ok:
        raise HTTPException(status_code=502, detail=error_msg or "텔레그램 연결에 실패했습니다.")
    return {"success": True}


@router.put("")
async def update_settings(body: SettingsUpdate):
    require_auth()
    to_save = {}
    for key, val in body.model_dump(exclude_none=True).items():
        if key == "DOWNLOAD_RULE":
            val = normalize_download_rule(val)
        to_save[key] = encrypt(val) if key in _SENSITIVE and val else val

    download_enabled = to_save.get("DOWNLOAD_ENABLED", Config.DOWNLOAD_ENABLED) == "true"
    download_rule = normalize_download_rule(to_save.get("DOWNLOAD_RULE", Config.get_download_rule()))
    stt_supported = download_enabled and download_rule in {"mp3", "both"}
    stt_enabled = to_save.get("STT_ENABLED", Config.STT_ENABLED) == "true"
    ai_enabled = to_save.get("AI_ENABLED", Config.AI_ENABLED) == "true"
    ai_agent = to_save.get("AI_AGENT", Config.AI_AGENT) or "gemini"
    has_ai_credentials = Config.has_ai_credentials(ai_agent, pending=to_save)
    if not download_enabled:
        to_save["AUTO_DOWNLOAD_AFTER_PLAY"] = "false"
        to_save["STT_ENABLED"] = "false"
        to_save["STT_DELETE_AUDIO_AFTER_TRANSCRIBE"] = "false"
        to_save["AI_ENABLED"] = "false"
        to_save["SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE"] = "false"
    elif not stt_supported:
        to_save["STT_ENABLED"] = "false"
        to_save["STT_DELETE_AUDIO_AFTER_TRANSCRIBE"] = "false"
        to_save["AI_ENABLED"] = "false"
        to_save["SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE"] = "false"
    elif not stt_enabled:
        to_save["STT_DELETE_AUDIO_AFTER_TRANSCRIBE"] = "false"
        to_save["AI_ENABLED"] = "false"
        to_save["SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE"] = "false"
    elif not ai_enabled or not has_ai_credentials:
        to_save["AI_ENABLED"] = "false"
        to_save["SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE"] = "false"

    if to_save:
        keys = sorted(to_save)
        before = event_log.setting_snapshot(keys)
        try:
            db.set_many(to_save)
            Config.load()
        except Exception as e:
            event_log.record_event(
                event_type="settings",
                action="update",
                status="failed",
                actor_user_id=app_state.user_id or None,
                error_code=type(e).__name__,
                error_message=str(e),
                metadata={"changed_keys": keys, "before": before, "attempted": to_save},
            )
            raise

        after = event_log.setting_snapshot(keys)
        event_log.record_event(
            event_type="settings",
            action="update",
            status="success",
            actor_user_id=app_state.user_id or None,
            message="설정이 저장되었습니다.",
            metadata={"changed_keys": event_log.changed_keys(before, after), "before": before, "after": after},
        )

    return {"success": True}
