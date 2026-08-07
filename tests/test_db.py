"""db.py 스키마 캐싱/경로 테스트."""

from pathlib import Path

from src import db


def test_connect_reuses_schema_cache_per_path(tmp_path, monkeypatch):
    """같은 경로는 스키마를 한 번만 생성하고, 다른 경로는 새로 생성해야 한다."""
    path_a = tmp_path / "a" / "app.db"
    path_b = tmp_path / "b" / "app.db"

    monkeypatch.setattr(db, "_schema_ready_paths", set())
    monkeypatch.setattr(db, "_db_path", lambda: path_a)
    db.set("key", "value")
    assert path_a.exists()

    monkeypatch.setattr(db, "_db_path", lambda: path_b)
    db.set("key", "value")
    assert path_b.exists()
    assert db.get("key") == "value"


def test_db_path_does_not_depend_on_cwd(monkeypatch):
    """/db가 없으면 CWD가 아니라 프로젝트 고정 경로를 기준으로 db/를 사용해야 한다."""
    monkeypatch.setattr(Path, "exists", lambda self: str(self) != "/db")
    path = db._db_path()
    assert path.is_absolute()
    assert path.parent.name == "db"
