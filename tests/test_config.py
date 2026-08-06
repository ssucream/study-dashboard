"""config.py / db.py 단위 테스트."""

from pathlib import Path
from unittest.mock import patch

from src.config import _default_download_dir, _read_version, normalize_download_rule


def test_read_version():
    """CHANGELOG.md에서 버전을 정상적으로 파싱한다."""
    version = _read_version()
    assert version != "unknown"
    parts = version.split("-")[0].split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_default_download_dir():
    """다운로드 기본 경로는 컨테이너 내부 /downloads로 고정한다."""
    assert _default_download_dir() == "/downloads"


def test_normalize_download_rule():
    """다운로드 규칙은 mp4/mp3/both 표준값으로 정규화된다."""
    assert normalize_download_rule("") == "mp4"
    assert normalize_download_rule(None) == "mp4"
    assert normalize_download_rule("video") == "mp4"
    assert normalize_download_rule("audio") == "mp3"
    assert normalize_download_rule("mp4") == "mp4"
    assert normalize_download_rule("mp3") == "mp3"
    assert normalize_download_rule("both") == "both"
    assert normalize_download_rule("unexpected") == "mp4"


# ── db.py 테스트 ───────────────────────────────────────────────


def _make_db(tmp_path: Path):
    """임시 경로에 격리된 DB 모듈 패치를 반환하는 컨텍스트."""
    import src.db as db_module

    return patch.object(db_module, "_db_path", return_value=tmp_path / "app.db")


def test_db_get_set(tmp_path):
    """set 후 get이 동일한 값을 반환한다."""
    import src.db as db

    with _make_db(tmp_path):
        db.set("FOO", "bar")
        assert db.get("FOO") == "bar"


def test_db_get_default(tmp_path):
    """존재하지 않는 키는 default를 반환한다."""
    import src.db as db

    with _make_db(tmp_path):
        assert db.get("MISSING") == ""
        assert db.get("MISSING", "fallback") == "fallback"


def test_config_load_does_not_auto_load_lms_credentials(tmp_path):
    """DB에 계정 정보가 남아 있어도 자동 로그인용으로 로드하지 않는다."""
    import src.db as db
    from src.config import Config

    with _make_db(tmp_path):
        db.set_many({"LMS_USER_ID": "student123", "LMS_PASSWORD": "secret"})
        Config.clear_session_credentials()
        Config.load()

    assert Config.LMS_USER_ID == ""
    assert Config.LMS_PASSWORD == ""


def test_config_load_preserves_session_credentials(tmp_path):
    """설정 reload는 현재 로그인 세션의 메모리 credential을 지우지 않는다."""
    import src.db as db
    from src.config import Config

    with _make_db(tmp_path):
        Config.set_session_credentials("student123", "secret")
        db.set("DOWNLOAD_RULE", "mp3")
        Config.load()

    assert Config.LMS_USER_ID == "student123"
    assert Config.LMS_PASSWORD == "secret"
    assert Config.DOWNLOAD_RULE == "mp3"


def test_config_ignores_saved_download_dir(tmp_path):
    """DB에 과거 경로가 저장되어 있어도 다운로드 경로는 /downloads만 사용한다."""
    import src.db as db
    from src.config import Config

    with _make_db(tmp_path):
        db.set("DOWNLOAD_DIR", "/tmp/old-download")
        Config.load()

    assert Config.DOWNLOAD_DIR == "/downloads"
    assert Config.get_download_dir() == "/downloads"


def test_config_loads_stt_delete_audio_option(tmp_path):
    """STT 후 mp3 삭제 옵션을 DB에서 로드한다."""
    import src.db as db
    from src.config import Config

    with _make_db(tmp_path):
        db.set_many(
            {
                "DOWNLOAD_ENABLED": "true",
                "DOWNLOAD_RULE": "mp3",
                "STT_ENABLED": "true",
                "STT_DELETE_AUDIO_AFTER_TRANSCRIBE": "true",
            }
        )
        Config.load()

    assert Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE == "true"


def test_config_load_disables_stt_for_mp4_rule(tmp_path):
    """mp4 다운로드 규칙에서는 저장된 STT 옵션도 비활성으로 정규화한다."""
    import src.db as db
    from src.config import Config

    with _make_db(tmp_path):
        db.set_many(
            {
                "DOWNLOAD_ENABLED": "true",
                "DOWNLOAD_RULE": "mp4",
                "STT_ENABLED": "true",
                "STT_DELETE_AUDIO_AFTER_TRANSCRIBE": "true",
                "AI_ENABLED": "true",
            }
        )
        Config.load()

    assert Config.STT_ENABLED == "false"
    assert Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE == "false"
    assert Config.AI_ENABLED == "false"


def test_config_load_disables_ai_without_gemini_key_or_model(tmp_path):
    """AI 요약은 Gemini API 키와 모델 설정이 모두 있어야 활성화된다."""
    import src.db as db
    from src.config import Config

    with _make_db(tmp_path):
        db.set_many(
            {
                "DOWNLOAD_ENABLED": "true",
                "DOWNLOAD_RULE": "mp3",
                "STT_ENABLED": "true",
                "AI_ENABLED": "true",
                "SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE": "true",
            }
        )
        Config.load()

    assert Config.AI_ENABLED == "false"
    assert Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE == "false"


def test_config_loads_default_summary_prompt(tmp_path):
    """요약 프롬프트 설정이 없으면 기본 프롬프트를 사용한다."""
    from src.config import Config
    from src.summarizer.summarizer import DEFAULT_SUMMARY_PROMPT

    with _make_db(tmp_path):
        Config.load()

    assert Config.get_summary_prompt_template() == DEFAULT_SUMMARY_PROMPT


def test_db_set_many(tmp_path):
    """set_many로 여러 값을 한 번에 저장한다."""
    import src.db as db

    with _make_db(tmp_path):
        db.set_many({"A": "1", "B": "2", "C": "3"})
        assert db.get("A") == "1"
        assert db.get("B") == "2"
        assert db.get("C") == "3"


def test_db_set_many_upsert(tmp_path):
    """기존 키는 갱신, 새 키는 삽입된다."""
    import src.db as db

    with _make_db(tmp_path):
        db.set("X", "old")
        db.set_many({"X": "new", "Y": "added"})
        assert db.get("X") == "new"
        assert db.get("Y") == "added"


def test_db_init_idempotent(tmp_path):
    """init()을 여러 번 호출해도 오류가 없어야 한다."""
    import src.db as db

    with _make_db(tmp_path):
        db.init()
        db.init()
        db.init()
