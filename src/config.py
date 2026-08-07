from datetime import timedelta, timezone
from pathlib import Path

from src import db
from src.crypto import decrypt, encrypt, is_encrypted

# ── 공용 상수 ─────────────────────────────────────────────────
KST = timezone(timedelta(hours=9))


def _load_credential(key: str) -> str:
    """DB에서 읽어 복호화한다. 복호화 실패 시 빈 문자열 반환."""
    raw = db.get(key)
    if not raw:
        return ""
    if is_encrypted(raw):
        return decrypt(raw)
    return raw


def _default_download_dir() -> str:
    """다운로드 경로는 UI/DB 설정과 무관하게 컨테이너 내부 /download로 고정한다."""
    return "/downloads"


# AI 요약 provider → 저장 필드 매핑. 신규 provider 추가 시 이 두 dict만 확장하면 됨.
_AGENT_KEY_ATTR = {
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
_AGENT_MODEL_ATTR = {
    "gemini": "GEMINI_MODEL",
    "openai": "OPENAI_MODEL",
    "openrouter": "OPENROUTER_MODEL",
}


def _read_version() -> str:
    """CHANGELOG.md의 첫 번째 ## [vX.Y.Z] 항목에서 버전을 읽어온다."""
    import re

    changelog = Path(__file__).parent.parent / "CHANGELOG.md"
    try:
        for line in changelog.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^## \[v(.+?)\]", line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "unknown"


APP_VERSION = _read_version()

_DOWNLOAD_RULE_ALIASES = {
    "": "mp4",
    "video": "mp4",
    "mp4": "mp4",
    "audio": "mp3",
    "mp3": "mp3",
    "both": "both",
}


def normalize_download_rule(rule: str | None) -> str:
    """다운로드 규칙을 웹 표준값(mp4/mp3/both)으로 정규화한다."""
    return _DOWNLOAD_RULE_ALIASES.get((rule or "").strip().lower(), "mp4")


def get_data_path(filename: str) -> Path:
    """데이터 파일 경로를 반환한다. Docker(/db) 또는 로컬(db/)."""
    base = Path("/db") if Path("/db").exists() else Path("db")
    return base / filename


def _default_summary_prompt() -> str:
    from src.summarizer.summarizer import DEFAULT_SUMMARY_PROMPT

    return DEFAULT_SUMMARY_PROMPT


class Config:
    # 클래스 정의 시점에는 기본값으로 초기화.
    # 앱 시작 시 Config.load()를 호출해 DB에서 실제 값을 로드한다.
    LMS_USER_ID: str = ""
    LMS_PASSWORD: str = ""
    GOOGLE_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    WHISPER_MODEL: str = "base"
    STT_LANGUAGE: str = "ko"
    STT_DELETE_AUDIO_AFTER_TRANSCRIBE: str = ""
    DOWNLOAD_ENABLED: str = ""
    DOWNLOAD_DIR: str = ""
    DOWNLOAD_RULE: str = "mp4"
    AUTO_DOWNLOAD_AFTER_PLAY: str = ""
    STT_ENABLED: str = ""
    AI_ENABLED: str = ""
    AI_AGENT: str = ""
    GEMINI_MODEL: str = ""
    OPENAI_MODEL: str = ""
    OPENROUTER_MODEL: str = ""
    SUMMARY_PROMPT_TEMPLATE: str = ""
    SUMMARY_PROMPT_EXTRA: str = ""
    SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE: str = ""
    TELEGRAM_ENABLED: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_AUTO_DELETE: str = ""

    @classmethod
    def load(cls) -> None:
        """DB에서 모든 설정을 로드한다. 앱 시작 시 반드시 1회 호출."""
        session_user_id = cls.LMS_USER_ID
        session_password = cls.LMS_PASSWORD
        # 보안상 LMS 계정은 DB에서 자동 로드하지 않는다.
        # 로그인 폼/CUI 입력으로 받은 값만 현재 프로세스 메모리에 유지한다.
        cls.LMS_USER_ID = session_user_id
        cls.LMS_PASSWORD = session_password
        cls.GOOGLE_API_KEY = _load_credential("GOOGLE_API_KEY")
        cls.OPENAI_API_KEY = _load_credential("OPENAI_API_KEY")
        cls.OPENROUTER_API_KEY = _load_credential("OPENROUTER_API_KEY")
        cls.TELEGRAM_BOT_TOKEN = _load_credential("TELEGRAM_BOT_TOKEN")
        cls.WHISPER_MODEL = db.get("WHISPER_MODEL", "base")
        cls.STT_LANGUAGE = db.get("STT_LANGUAGE", "ko")
        cls.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = db.get("STT_DELETE_AUDIO_AFTER_TRANSCRIBE", "false")
        cls.DOWNLOAD_ENABLED = db.get("DOWNLOAD_ENABLED", "false")
        cls.DOWNLOAD_DIR = _default_download_dir()
        cls.DOWNLOAD_RULE = normalize_download_rule(db.get("DOWNLOAD_RULE", "mp4"))
        cls.AUTO_DOWNLOAD_AFTER_PLAY = db.get("AUTO_DOWNLOAD_AFTER_PLAY", "false")
        cls.STT_ENABLED = db.get("STT_ENABLED", "")
        if cls.DOWNLOAD_ENABLED != "true" or cls.DOWNLOAD_RULE not in {"mp3", "both"}:
            cls.STT_ENABLED = "false"
        cls.AI_ENABLED = db.get("AI_ENABLED", "")
        if cls.STT_ENABLED != "true":
            cls.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = "false"
            cls.AI_ENABLED = "false"
        cls.AI_AGENT = db.get("AI_AGENT", "") or "gemini"
        cls.GEMINI_MODEL = db.get("GEMINI_MODEL", "")
        cls.OPENAI_MODEL = db.get("OPENAI_MODEL", "")
        cls.OPENROUTER_MODEL = db.get("OPENROUTER_MODEL", "")
        cls.SUMMARY_PROMPT_TEMPLATE = db.get("SUMMARY_PROMPT_TEMPLATE", _default_summary_prompt())
        cls.SUMMARY_PROMPT_EXTRA = db.get("SUMMARY_PROMPT_EXTRA", "")
        cls.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = db.get("SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE", "false")
        if cls.AI_ENABLED == "true" and (not cls.get_ai_api_key() or not cls.get_ai_model()):
            cls.AI_ENABLED = "false"
            cls.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = "false"
        cls.TELEGRAM_ENABLED = db.get("TELEGRAM_ENABLED", "")
        cls.TELEGRAM_CHAT_ID = db.get("TELEGRAM_CHAT_ID", "")
        cls.TELEGRAM_AUTO_DELETE = db.get("TELEGRAM_AUTO_DELETE", "")

    @classmethod
    def get_ai_api_key(cls, agent: str | None = None) -> str:
        """agent(생략 시 현재 AI_AGENT)에 해당하는 API 키를 반환한다."""
        return getattr(cls, _AGENT_KEY_ATTR.get(agent or cls.AI_AGENT, "GOOGLE_API_KEY"))

    @classmethod
    def get_ai_model(cls, agent: str | None = None) -> str:
        """agent(생략 시 현재 AI_AGENT)에 해당하는 모델을 반환한다."""
        return getattr(cls, _AGENT_MODEL_ATTR.get(agent or cls.AI_AGENT, "GEMINI_MODEL"))

    @classmethod
    def has_ai_credentials(cls, agent: str, pending: dict | None = None) -> bool:
        """agent의 API 키·모델이 (저장 대기 중인 값 포함) 모두 설정되어 있는지 확인한다."""
        pending = pending or {}
        key_field = _AGENT_KEY_ATTR.get(agent, "GOOGLE_API_KEY")
        model_field = _AGENT_MODEL_ATTR.get(agent, "GEMINI_MODEL")
        has_key = bool(pending.get(key_field) or getattr(cls, key_field))
        has_model = bool(pending.get(model_field) or getattr(cls, model_field))
        return has_key and has_model

    @classmethod
    def get_telegram_credentials(cls) -> tuple[str, str] | None:
        """텔레그램이 활성화되고 credential이 유효하면 (token, chat_id) 반환."""
        if cls.TELEGRAM_ENABLED != "true":
            return None
        if not cls.TELEGRAM_BOT_TOKEN or not cls.TELEGRAM_CHAT_ID:
            return None
        return cls.TELEGRAM_BOT_TOKEN, cls.TELEGRAM_CHAT_ID

    @classmethod
    def has_settings(cls) -> bool:
        """최초 설정이 완료됐는지 확인 (다운로드 규칙 기준)."""
        return bool(cls.get_download_rule())

    @classmethod
    def get_download_dir(cls) -> str:
        """다운로드 경로는 컨테이너 내부 /download로 고정한다."""
        return _default_download_dir()

    @classmethod
    def get_download_rule(cls) -> str:
        """다운로드 규칙을 mp4/mp3/both 중 하나로 정규화해 반환한다."""
        return normalize_download_rule(cls.DOWNLOAD_RULE)

    @classmethod
    def get_summary_prompt_template(cls) -> str:
        """현재 요약 프롬프트 템플릿을 반환한다."""
        return cls.SUMMARY_PROMPT_TEMPLATE or _default_summary_prompt()

    @classmethod
    def is_download_enabled(cls) -> bool:
        return cls.DOWNLOAD_ENABLED == "true"

    @classmethod
    def is_auto_download_after_play_enabled(cls) -> bool:
        return cls.is_download_enabled() and cls.AUTO_DOWNLOAD_AFTER_PLAY == "true"

    @classmethod
    def save_settings(
        cls,
        download_dir: str,
        download_rule: str,
        stt_enabled: bool,
        ai_enabled: bool,
        ai_agent: str,
        api_key: str,
        ai_model: str = "",
        summary_prompt_template: str = "",
        summary_prompt_extra: str = "",
        download_enabled: bool = True,
        auto_download_after_play: bool = True,
        stt_delete_audio_after_transcribe: bool = False,
        summary_delete_text_after_summarize: bool = False,
    ) -> None:
        """설정 항목을 DB에 저장한다."""
        ai_agent = ai_agent or "gemini"
        key_attr = _AGENT_KEY_ATTR.get(ai_agent, "GOOGLE_API_KEY")
        model_attr = _AGENT_MODEL_ATTR.get(ai_agent, "GEMINI_MODEL")

        auto_download_after_play = auto_download_after_play and download_enabled
        normalized_rule = normalize_download_rule(download_rule)
        stt_enabled = stt_enabled and download_enabled and normalized_rule in {"mp3", "both"}
        ai_enabled = ai_enabled and stt_enabled and bool(api_key) and bool(ai_model)
        if not stt_enabled:
            ai_enabled = False

        cls.DOWNLOAD_ENABLED = "true" if download_enabled else "false"
        fixed_download_dir = cls.get_download_dir()
        cls.DOWNLOAD_DIR = fixed_download_dir
        cls.DOWNLOAD_RULE = normalized_rule
        cls.AUTO_DOWNLOAD_AFTER_PLAY = "true" if auto_download_after_play else "false"
        cls.STT_ENABLED = "true" if stt_enabled else "false"
        cls.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = "true" if stt_enabled and stt_delete_audio_after_transcribe else "false"
        cls.AI_ENABLED = "true" if ai_enabled else "false"
        cls.AI_AGENT = ai_agent
        cls.SUMMARY_PROMPT_TEMPLATE = summary_prompt_template or _default_summary_prompt()
        cls.SUMMARY_PROMPT_EXTRA = summary_prompt_extra
        cls.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = (
            "true" if ai_enabled and summary_delete_text_after_summarize else "false"
        )
        if ai_model:
            setattr(cls, model_attr, ai_model)

        to_save: dict = {
            "DOWNLOAD_DIR": fixed_download_dir,
            "DOWNLOAD_RULE": cls.DOWNLOAD_RULE,
            "DOWNLOAD_ENABLED": cls.DOWNLOAD_ENABLED,
            "AUTO_DOWNLOAD_AFTER_PLAY": cls.AUTO_DOWNLOAD_AFTER_PLAY,
            "STT_ENABLED": cls.STT_ENABLED,
            "STT_DELETE_AUDIO_AFTER_TRANSCRIBE": cls.STT_DELETE_AUDIO_AFTER_TRANSCRIBE,
            "AI_ENABLED": cls.AI_ENABLED,
            "AI_AGENT": ai_agent,
            "SUMMARY_PROMPT_TEMPLATE": cls.SUMMARY_PROMPT_TEMPLATE,
            "SUMMARY_PROMPT_EXTRA": summary_prompt_extra,
            "SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE": cls.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE,
        }
        if ai_model:
            to_save[model_attr] = ai_model
        if ai_enabled:
            setattr(cls, key_attr, api_key)
            to_save[key_attr] = encrypt(api_key) if api_key else ""

        db.set_many(to_save)

    @classmethod
    def save_telegram(cls, enabled: bool, bot_token: str, chat_id: str, auto_delete: bool) -> None:
        """텔레그램 설정을 DB에 저장한다."""
        cls.TELEGRAM_ENABLED = "true" if enabled else "false"
        cls.TELEGRAM_BOT_TOKEN = bot_token
        cls.TELEGRAM_CHAT_ID = chat_id
        cls.TELEGRAM_AUTO_DELETE = "true" if auto_delete else "false"
        db.set_many(
            {
                "TELEGRAM_ENABLED": cls.TELEGRAM_ENABLED,
                "TELEGRAM_BOT_TOKEN": encrypt(bot_token) if bot_token else "",
                "TELEGRAM_CHAT_ID": chat_id,
                "TELEGRAM_AUTO_DELETE": cls.TELEGRAM_AUTO_DELETE,
            }
        )

    @classmethod
    def set_session_credentials(cls, user_id: str, password: str) -> None:
        """현재 프로세스 세션에서만 LMS 계정 정보를 보관한다."""
        cls.LMS_USER_ID = user_id
        cls.LMS_PASSWORD = password

    @classmethod
    def clear_session_credentials(cls) -> None:
        """메모리에 보관 중인 LMS 계정 정보를 지운다."""
        cls.LMS_USER_ID = ""
        cls.LMS_PASSWORD = ""

    @classmethod
    def _save_settings_values(cls, keys_to_update: dict) -> None:
        """지정한 키/값을 DB에 저장한다.

        settings.py 등에서 직접 호출하는 경우를 위해 유지.
        """
        db.set_many(keys_to_update)
