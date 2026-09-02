"""웹 설정 route 다운로드 종속 옵션 테스트."""

import pytest
from backend.api.routes import settings as settings_route
from backend.api.state import app_state

from src.config import Config
from src.event_log import list_events
from src.summarizer.summarizer import DEFAULT_SUMMARY_PROMPT


class _FakeScraper:
    pass


def _make_db(tmp_path, monkeypatch):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")


@pytest.fixture(autouse=True)
def reset_state():
    app_state.scraper = _FakeScraper()
    Config.DOWNLOAD_ENABLED = "true"
    Config.DOWNLOAD_RULE = "mp4"
    Config.AUTO_DOWNLOAD_AFTER_PLAY = "true"
    Config.STT_ENABLED = "false"
    Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE = "false"
    Config.AI_ENABLED = "false"
    Config.GOOGLE_API_KEY = ""
    Config.GEMINI_MODEL = ""
    Config.SUMMARY_PROMPT_TEMPLATE = DEFAULT_SUMMARY_PROMPT
    Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE = "false"
    yield
    app_state.scraper = None


@pytest.mark.asyncio
async def test_update_settings_disables_dependent_options_when_download_off(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="false",
            AUTO_DOWNLOAD_AFTER_PLAY="true",
            STT_ENABLED="true",
            AI_ENABLED="true",
        )
    )

    assert saved["DOWNLOAD_ENABLED"] == "false"
    assert saved["AUTO_DOWNLOAD_AFTER_PLAY"] == "false"
    assert saved["STT_ENABLED"] == "false"
    assert saved["AI_ENABLED"] == "false"


@pytest.mark.asyncio
async def test_update_settings_allows_stt_when_auto_download_off(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="true",
            DOWNLOAD_RULE="mp3",
            AUTO_DOWNLOAD_AFTER_PLAY="false",
            STT_ENABLED="true",
            AI_ENABLED="true",
        )
    )

    assert saved["DOWNLOAD_ENABLED"] == "true"
    assert saved["AUTO_DOWNLOAD_AFTER_PLAY"] == "false"
    assert saved["STT_ENABLED"] == "true"
    assert saved["AI_ENABLED"] == "false"


@pytest.mark.asyncio
async def test_update_settings_disables_stt_when_download_rule_is_mp4(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="true",
            DOWNLOAD_RULE="mp4",
            STT_ENABLED="true",
            STT_DELETE_AUDIO_AFTER_TRANSCRIBE="true",
            AI_ENABLED="true",
        )
    )

    assert saved["DOWNLOAD_RULE"] == "mp4"
    assert saved["STT_ENABLED"] == "false"
    assert saved["STT_DELETE_AUDIO_AFTER_TRANSCRIBE"] == "false"
    assert saved["AI_ENABLED"] == "false"


@pytest.mark.asyncio
async def test_update_settings_ignores_download_dir(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate.model_validate(
            {
                "DOWNLOAD_ENABLED": "true",
                "DOWNLOAD_DIR": "/tmp/custom",
                "DOWNLOAD_RULE": "mp3",
            }
        )
    )

    assert saved["DOWNLOAD_ENABLED"] == "true"
    assert saved["DOWNLOAD_RULE"] == "mp3"
    assert "DOWNLOAD_DIR" not in saved


@pytest.mark.asyncio
async def test_update_settings_writes_masked_event_log(monkeypatch, tmp_path):
    _make_db(tmp_path, monkeypatch)
    app_state.user_id = "student"

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_RULE="mp3",
            GOOGLE_API_KEY="real-secret-key",
        )
    )

    events = list_events(event_type="settings", limit=1)

    assert events[0]["action"] == "update"
    assert events[0]["status"] == "success"
    assert events[0]["actor_user_id"] == "student"
    assert events[0]["metadata"]["after"]["GOOGLE_API_KEY"] == "[redacted]"
    assert "real-secret-key" not in str(events[0]["metadata"])


@pytest.mark.asyncio
async def test_update_settings_disables_ai_without_gemini_key_and_model(monkeypatch):
    saved = {}
    Config.STT_ENABLED = "true"

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="true",
            DOWNLOAD_RULE="mp3",
            STT_ENABLED="true",
            AI_ENABLED="true",
            SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE="true",
        )
    )

    assert saved["AI_ENABLED"] == "false"
    assert saved["SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE"] == "false"


@pytest.mark.asyncio
async def test_update_settings_allows_ai_with_gemini_key_and_model(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="true",
            DOWNLOAD_RULE="mp3",
            STT_ENABLED="true",
            AI_ENABLED="true",
            GOOGLE_API_KEY="real-secret-key",
            GEMINI_MODEL="gemini-2.5-flash",
            SUMMARY_PROMPT_TEMPLATE="커스텀 {text}",
            SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE="true",
        )
    )

    assert saved["AI_ENABLED"] == "true"
    assert saved["GEMINI_MODEL"] == "gemini-2.5-flash"
    assert saved["SUMMARY_PROMPT_TEMPLATE"] == "커스텀 {text}"
    assert saved["SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE"] == "true"


@pytest.mark.asyncio
async def test_get_settings_returns_summary_prompt_default():
    payload = await settings_route.get_settings()

    assert payload["SUMMARY_PROMPT_DEFAULT"] == DEFAULT_SUMMARY_PROMPT
    assert payload["SUMMARY_PROMPT_TEMPLATE"] == DEFAULT_SUMMARY_PROMPT
    assert "SUMMARY_PROMPT_EXTRA" in payload
    assert "CHAPEL_SUMMARY_ENABLED" in payload


@pytest.mark.asyncio
async def test_update_settings_persists_extra_prompt_and_chapel_toggle(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="true",
            DOWNLOAD_RULE="mp3",
            STT_ENABLED="true",
            AI_ENABLED="true",
            GOOGLE_API_KEY="real-secret-key",
            GEMINI_MODEL="gemini-2.5-flash",
            SUMMARY_PROMPT_EXTRA="영어 용어는 원문 유지",
            CHAPEL_SUMMARY_ENABLED="false",
        )
    )

    assert saved["SUMMARY_PROMPT_EXTRA"] == "영어 용어는 원문 유지"
    assert saved["CHAPEL_SUMMARY_ENABLED"] == "false"


@pytest.mark.asyncio
async def test_update_settings_can_clear_extra_prompt(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            DOWNLOAD_ENABLED="true",
            DOWNLOAD_RULE="mp3",
            STT_ENABLED="true",
            AI_ENABLED="true",
            GOOGLE_API_KEY="real-secret-key",
            GEMINI_MODEL="gemini-2.5-flash",
            SUMMARY_PROMPT_EXTRA="",
        )
    )

    assert saved["SUMMARY_PROMPT_EXTRA"] == ""


@pytest.mark.asyncio
async def test_update_settings_persists_telegram_notify_toggles(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(
        settings_route.SettingsUpdate(
            TELEGRAM_NOTIFY_PLAYBACK="false",
            TELEGRAM_NOTIFY_SUMMARY="true",
            TELEGRAM_NOTIFY_ERROR="false",
            TELEGRAM_NOTIFY_DEADLINE="true",
            TELEGRAM_DEADLINE_THRESHOLDS="24, 12 ,bogus,168",
        )
    )

    assert saved["TELEGRAM_NOTIFY_PLAYBACK"] == "false"
    assert saved["TELEGRAM_NOTIFY_ERROR"] == "false"
    # 내림차순 정규화 + 잘못된 토큰 제거
    assert saved["TELEGRAM_DEADLINE_THRESHOLDS"] == "168,24,12"


@pytest.mark.asyncio
async def test_update_settings_deadline_thresholds_fallback_to_default(monkeypatch):
    saved = {}

    monkeypatch.setattr(settings_route.db, "set_many", lambda values: saved.update(values))
    monkeypatch.setattr(Config, "load", lambda: None)

    await settings_route.update_settings(settings_route.SettingsUpdate(TELEGRAM_DEADLINE_THRESHOLDS="nonsense"))

    assert saved["TELEGRAM_DEADLINE_THRESHOLDS"] == "168,72,24,12"


@pytest.mark.asyncio
async def test_get_settings_returns_telegram_notify_fields():
    payload = await settings_route.get_settings()

    for key in (
        "TELEGRAM_NOTIFY_PLAYBACK",
        "TELEGRAM_NOTIFY_SUMMARY",
        "TELEGRAM_NOTIFY_ERROR",
        "TELEGRAM_NOTIFY_DEADLINE",
        "TELEGRAM_DEADLINE_THRESHOLDS",
    ):
        assert key in payload


@pytest.mark.asyncio
async def test_get_ai_models_returns_curated_and_live_catalogs(monkeypatch):
    def fake_catalog(agent, api_key=""):
        return ([{"id": f"{agent}/latest", "name": f"{agent} latest"}], "live" if api_key else "curated")

    monkeypatch.setattr(Config, "OPENROUTER_API_KEY", "stored-key")
    monkeypatch.setattr(settings_route, "get_model_catalog", fake_catalog)

    payload = await settings_route.get_ai_models()

    assert payload["as_of"] == "2026-08"
    assert payload["providers"]["gemini"]["models"][0]["id"] == "gemini/latest"
    assert payload["providers"]["openrouter"]["models"][0]["id"] == "openrouter/latest"
    assert payload["providers"]["openrouter"]["source"] == "live"
