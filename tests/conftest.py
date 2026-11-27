"""모든 테스트가 실제 db/app.db·logs/ 대신 임시 경로를 쓰도록 격리한다."""

import pytest

from src import db
from src import logger as logger_module


def pytest_configure(config):
    config.addinivalue_line("markers", "real_db_path: 실제 db._db_path()를 그대로 검사하는 테스트")


@pytest.fixture(autouse=True)
def _isolate_db_and_logs(request, tmp_path, monkeypatch):
    if "real_db_path" not in request.keywords:
        monkeypatch.setattr(db, "_db_path", lambda: tmp_path / "db" / "app.db")
    monkeypatch.setattr(logger_module, "_LOGS_DIR", tmp_path / "logs")
