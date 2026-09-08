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
RESULT_VERIFIED = "verified"  # 출석 확인 — 행 삭제(이력 초기화)
RESULT_UNVERIFIED = "unverified"  # 검증 불가 — 상시화되면 결국 억제
RESULT_FAILED = "failed"  # 재스크래핑으로 출석 미반영 확정 — 억제 대상
RESULT_ERROR = "error"  # 일시적 재생 오류(크래시/타임아웃 등) — 억제 카운트에서 제외

# attempt_count를 올려 억제 판정에 반영하는 결과. 여기 없는 결과는 관측만 하고 카운트하지 않는다.
# 브라우저 크래시·ErrAlreadyInView 같은 일시적 실패로 정상 강의가 억제되면 안 되기 때문이다.
_SUPPRESSING_RESULTS = frozenset({RESULT_UNVERIFIED, RESULT_FAILED})

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

    RESULT_VERIFIED면 행을 삭제해 이력을 초기화한다 (성공 = 이전 실패 무효).
    _SUPPRESSING_RESULTS(unverified/failed)면 attempt_count를 1 올리고,
    max_attempts에 도달하면 억제한다. max_attempts <= 0이면 억제하지 않는다 (무제한 재시도).
    RESULT_ERROR는 마지막 시도 정보만 갱신하고 attempt_count를 올리지 않는다 —
    일시적 재생 오류로 정상 강의가 억제되는 것을 막기 위함이다.

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
                "SELECT attempt_count, suppressed FROM playback_attempts WHERE course_id = ? AND lecture_url = ?",
                (course_id, lecture_url),
            ).fetchone()
            prev_count = row["attempt_count"] if row else 0
            was_suppressed = bool(row["suppressed"]) if row else False

            counts_toward_suppression = result in _SUPPRESSING_RESULTS
            count = prev_count + 1 if counts_toward_suppression else prev_count
            suppress_now = counts_toward_suppression and max_attempts > 0 and count >= max_attempts
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
                rows = conn.execute("SELECT lecture_url FROM playback_attempts WHERE suppressed = 1").fetchall()
            return {row["lecture_url"] for row in rows}
    except Exception:
        return set()


def suppressed_count() -> int:
    """억제된 강의 수. 배지 표시용 — URL 집합을 만들 필요가 없을 때 쓴다."""
    try:
        with db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM playback_attempts WHERE suppressed = 1").fetchone()
            return int(row["n"]) if row else 0
    except Exception:
        return 0


def list_suppressed() -> list[dict[str, Any]]:
    """억제된 강의 목록 (API/UI용). 최근 시도 순."""
    try:
        with db._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM playback_attempts WHERE suppressed = 1 ORDER BY last_attempt_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        return []


def claim_notification(course_id: str, lecture_url: str) -> bool:
    """억제 알림 발송 권한을 선점한다. 아직 발송 전이었으면 notified=1로 표시하고 True.

    `notified`를 실제 가드로 읽으므로, 백엔드 재시작이나 중복 호출로 같은 강의에 대해
    억제 알림이 두 번 나가지 않는다. (`suppressed_now` 하나만으로는 프로세스 안에서만
    유효해 재시작 후 재억제 시 다시 발송된다.)
    """
    try:
        with db._connect() as conn:
            cur = conn.execute(
                "UPDATE playback_attempts SET notified = 1 WHERE course_id = ? AND lecture_url = ? AND notified = 0",
                (course_id, lecture_url),
            )
            return cur.rowcount > 0
    except Exception:
        return False


def reset(course_id: str | None = None, lecture_url: str | None = None) -> int:
    """억제를 해제한다 (행 삭제). 인자를 모두 생략하면 전체 해제. 삭제된 행 수 반환.

    lecture_url만 주고 course_id를 생략하는 것은 허용하지 않는다 — 조용히 전체 삭제로
    떨어지면 사용자가 강의 1개를 해제하려다 원장 전체를 날리게 된다.
    """
    if lecture_url and not course_id:
        return 0
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
