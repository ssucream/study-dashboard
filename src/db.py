"""SQLite 기반 설정 저장소.

db/app.db (Docker: /db/app.db) 에 key-value 방식으로 설정을 저장한다.
민감값은 호출 쪽에서 crypto.py로 암호화한 뒤 전달해야 한다.

테이블 생성은 _connect() 내부에서 자동으로 수행되므로 별도 init() 호출 없이도
get/set을 즉시 사용할 수 있다. init()는 명시적 초기화가 필요한 경우를 위해 공개한다.
"""

import sqlite3
from pathlib import Path

_schema_ready_paths: set[str] = set()


def _db_path() -> Path:
    base = Path("/db") if Path("/db").exists() else Path(__file__).resolve().parent.parent / "db"
    return base / "app.db"


def _connect() -> sqlite3.Connection:
    """DB 연결을 반환한다. 테이블이 없으면 자동 생성한다."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    key = str(path.resolve())
    if key not in _schema_ready_paths:
        _ensure_schema(conn)
        _schema_ready_paths.add(key)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """앱에서 사용하는 SQLite 테이블을 생성한다."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id       TEXT NOT NULL UNIQUE,
            created_at     TEXT NOT NULL,
            actor_user_id  TEXT,
            session_id     TEXT,
            event_type     TEXT NOT NULL,
            action         TEXT NOT NULL,
            status         TEXT NOT NULL,
            target_type    TEXT,
            target_id      TEXT,
            course_id      TEXT,
            course_name    TEXT,
            lecture_title  TEXT,
            lecture_url    TEXT,
            week_label     TEXT,
            message        TEXT,
            error_code     TEXT,
            error_message  TEXT,
            log_path       TEXT,
            metadata_json  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_logs_created_at ON event_logs(created_at DESC)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_event_logs_event_type_created_at ON event_logs(event_type, created_at DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event_logs_status_created_at ON event_logs(status, created_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id            TEXT PRIMARY KEY,
            kind          TEXT NOT NULL,
            status        TEXT NOT NULL,
            stage         TEXT NOT NULL,
            message       TEXT NOT NULL DEFAULT '',
            progress_pct  REAL,
            result_json   TEXT NOT NULL DEFAULT '{}',
            error         TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_kind_created_at ON tasks(kind, created_at DESC)")


def init() -> None:
    """DB 및 settings 테이블을 명시적으로 초기화한다. 앱 시작 시 호출 권장."""
    with _connect():
        pass


def get(key: str, default: str = "") -> str:
    """키에 해당하는 값을 반환한다. 없으면 default 반환."""
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set(key: str, value: str) -> None:
    """단일 키-값을 저장한다 (없으면 삽입, 있으면 갱신)."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE
                SET value      = excluded.value,
                    updated_at = excluded.updated_at
            """,
            (key, value),
        )


def persist_task(
    task_id: str,
    kind: str,
    status: str,
    stage: str,
    message: str,
    progress_pct: float | None,
    result_json: str,
    error: str | None,
    metadata_json: str,
    created_at: str,
    updated_at: str,
) -> None:
    """완료/실패된 task를 DB에 저장한다."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks
                (id, kind, status, stage, message, progress_pct, result_json, error,
                 metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status        = excluded.status,
                stage         = excluded.stage,
                message       = excluded.message,
                progress_pct  = excluded.progress_pct,
                result_json   = excluded.result_json,
                error         = excluded.error,
                updated_at    = excluded.updated_at
            """,
            (
                task_id,
                kind,
                status,
                stage,
                message,
                progress_pct,
                result_json,
                error,
                metadata_json,
                created_at,
                updated_at,
            ),
        )


def load_tasks(limit: int = 200) -> list[dict]:
    """최근 task 목록을 DB에서 로드한다."""
    import json

    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for field in ("result_json", "metadata_json"):
            try:
                d[field] = json.loads(d[field] or "{}")
            except Exception:
                d[field] = {}
        result.append(d)
    return result


def purge_old_tasks(days: int = 7) -> int:
    """N일보다 오래된 task를 삭제한다. 삭제된 행 수를 반환한다."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        return cur.rowcount


def set_many(pairs: dict) -> None:
    """여러 키-값을 단일 트랜잭션으로 저장한다."""
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE
                SET value      = excluded.value,
                    updated_at = excluded.updated_at
            """,
            [(k, v) for k, v in pairs.items()],
        )
