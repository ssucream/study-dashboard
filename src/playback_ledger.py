"""재생 재시도 억제 원장.

자동 모드가 같은 강의를 무한히 재시도하며 AI 토큰과 CPU를 태우는 것을 막는다.
강의별 시도 이력을 `playback_attempts` 테이블에 남기고, 출석 미반영이 설정된 횟수만큼
반복되면 `suppressed=1`로 표시해 자동 모드 pending에서 제외한다.

`event_log.py`와 같은 best-effort 정책이다 — 원장 기록 실패가 재생/파이프라인 같은
본 기능을 막지 않도록 모든 공개 함수가 예외를 삼킨다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src import db
from src.config import KST

# record_attempt(result=...)에 넘길 수 있는 값
RESULT_VERIFIED = "verified"
RESULT_UNVERIFIED = "unverified"
RESULT_FAILED = "failed"

_COLUMNS = (
    "course_id, lecture_url, course_name, lecture_title, week_label, "
    "attempt_count, last_result, last_error, last_attempt_at, "
    "suppressed, suppressed_at, notified"
)


def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def record_attempt(
    course_id: str,
    lecture_url: str,
    *,
    course_name: str = "",
    lecture_title: str = "",
    week_label: str = "",
    result: str,
    error: str | None = None,
    max_attempts: int = 3,
) -> tuple[int, bool]:
    """재생 시도 결과를 원장에 기록한다.

    result == "verified"면 행을 삭제해 이력을 초기화한다 (성공 = 이전 실패 무효).
    그 외에는 attempt_count를 1 올리고, max_attempts에 도달하면 억제한다.
    max_attempts <= 0이면 억제하지 않는다 (무제한 재시도).

    반환: (attempt_count, suppressed_now)
        suppressed_now는 **이번 호출에서 처음** 억제된 경우에만 True다.
        (호출자가 텔레그램 알림을 1회만 보내도록 판별하는 데 쓴다.)
    """
    try:
        with db._connect() as conn:
            if result == RESULT_VERIFIED:
                conn.execute(
                    "DELETE FROM playback_attempts WHERE course_id = ? AND lecture_url = ?",
                    (course_id, lecture_url),
                )
                return 0, False

            row = conn.execute(
                "SELECT attempt_count, suppressed FROM playback_attempts "
                "WHERE course_id = ? AND lecture_url = ?",
                (course_id, lecture_url),
            ).fetchone()
            prev_count = row["attempt_count"] if row else 0
            was_suppressed = bool(row["suppressed"]) if row else False

            count = prev_count + 1
            suppress_now = max_attempts > 0 and count >= max_attempts
            suppressed = 1 if (suppress_now or was_suppressed) else 0
            now = _now()

            conn.execute(
                f"""
                INSERT INTO playback_attempts ({_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(course_id, lecture_url) DO UPDATE SET
                    course_name     = excluded.course_name,
                    lecture_title   = excluded.lecture_title,
                    week_label      = excluded.week_label,
                    attempt_count   = excluded.attempt_count,
                    last_result     = excluded.last_result,
                    last_error      = excluded.last_error,
                    last_attempt_at = excluded.last_attempt_at,
                    suppressed      = excluded.suppressed,
                    suppressed_at   = excluded.suppressed_at
                """,
                (
                    course_id,
                    lecture_url,
                    course_name,
                    lecture_title,
                    week_label,
                    count,
                    result,
                    error,
                    now,
                    suppressed,
                    now if suppressed else None,
                ),
            )
            return count, bool(suppressed and not was_suppressed)
    except Exception:
        return 0, False


def is_suppressed(course_id: str, lecture_url: str) -> bool:
    """해당 강의가 자동 모드에서 제외됐는지."""
    try:
        with db._connect() as conn:
            row = conn.execute(
                "SELECT suppressed FROM playback_attempts WHERE course_id = ? AND lecture_url = ?",
                (course_id, lecture_url),
            ).fetchone()
            return bool(row and row["suppressed"])
    except Exception:
        return False


def suppressed_urls(course_id: str | None = None) -> set[str]:
    """억제된 강의 URL 집합. 사이클 시작 시 1회 조회해 강의마다 DB 왕복하는 것을 막는다."""
    try:
        with db._connect() as conn:
            if course_id:
                rows = conn.execute(
                    "SELECT lecture_url FROM playback_attempts WHERE suppressed = 1 AND course_id = ?",
                    (course_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT lecture_url FROM playback_attempts WHERE suppressed = 1"
                ).fetchall()
            return {row["lecture_url"] for row in rows}
    except Exception:
        return set()


def list_suppressed() -> list[dict[str, Any]]:
    """억제된 강의 목록 (API/UI용). 최근 시도 순."""
    try:
        with db._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM playback_attempts WHERE suppressed = 1 "
                "ORDER BY last_attempt_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        return []


def mark_notified(course_id: str, lecture_url: str) -> None:
    """텔레그램 억제 알림 발송 완료를 기록한다 (중복 발송 방지)."""
    try:
        with db._connect() as conn:
            conn.execute(
                "UPDATE playback_attempts SET notified = 1 WHERE course_id = ? AND lecture_url = ?",
                (course_id, lecture_url),
            )
    except Exception:
        pass


def reset(course_id: str | None = None, lecture_url: str | None = None) -> int:
    """억제를 해제한다 (행 삭제). 인자를 모두 생략하면 전체 해제. 삭제된 행 수 반환."""
    try:
        with db._connect() as conn:
            if course_id and lecture_url:
                cur = conn.execute(
                    "DELETE FROM playback_attempts WHERE course_id = ? AND lecture_url = ?",
                    (course_id, lecture_url),
                )
            elif course_id:
                cur = conn.execute("DELETE FROM playback_attempts WHERE course_id = ?", (course_id,))
            else:
                cur = conn.execute("DELETE FROM playback_attempts")
            return cur.rowcount
    except Exception:
        return 0
