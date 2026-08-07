"""src/updater.py 단위 테스트."""

from src.updater import _DOCKERHUB_TAGS_URL, _parse_version, check_update


def test_dockerhub_tags_url_points_to_study_dashboard():
    """study-helper(다른 프로젝트) 저장소를 잘못 참조하는 회귀를 방지한다."""
    assert "study-dashboard" in _DOCKERHUB_TAGS_URL
    assert "study-helper" not in _DOCKERHUB_TAGS_URL


def test_parse_version():
    assert _parse_version("v1.2.3") == (1, 2, 3)
    assert _parse_version("1.2.3") == (1, 2, 3)
    assert _parse_version("latest") is None


def test_check_update_returns_none_on_fetch_failure(monkeypatch):
    import src.updater as updater_module

    monkeypatch.setattr(updater_module, "fetch_latest_version", lambda timeout=5.0: None)
    assert check_update("26.7.2") is None


def test_check_update_returns_latest_when_newer(monkeypatch):
    import src.updater as updater_module

    monkeypatch.setattr(updater_module, "fetch_latest_version", lambda timeout=5.0: "v26.8.0")
    assert check_update("26.7.2") == "v26.8.0"


def test_check_update_returns_none_when_not_newer(monkeypatch):
    import src.updater as updater_module

    monkeypatch.setattr(updater_module, "fetch_latest_version", lambda timeout=5.0: "v26.7.2")
    assert check_update("26.7.2") is None
