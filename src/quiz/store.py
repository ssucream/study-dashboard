"""예상 문제셋 / 문항 / 풀이 이력의 SQLite 저장소.

요약 저장소(summary_store)는 파일 기반이지만, 문제셋은 관계형 구조(문제셋 1 : 문항 N :
풀이 N)이고 재시작 후에도 오답 이력을 조회해야 하므로 SQLite에 저장한다.
스키마는 src/db.py의 _ensure_schema()에서 생성된다.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from src import db
from src.quiz.models import GradeResult, QuizQuestion, QuizSet


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def create_quiz_set(
    *,
    course_id: str,
    course_name: str,
    term: str,
    weeks: list[str],
    counts: dict[str, int],
    source: dict[str, Any],
    agent: str,
    model: str,
    questions: list[QuizQuestion],
    status: str = "ready",
) -> QuizSet:
    """문제셋과 문항들을 한 트랜잭션으로 저장한다."""
    set_id = _new_id()
    created_at = _now_iso()
    for idx, q in enumerate(questions):
        q.ordinal = idx
        if not q.id:
            q.id = _new_id()

    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO quiz_sets
                (id, course_id, course_name, term, weeks_json, counts_json,
                 source_json, agent, model, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                set_id,
                course_id,
                course_name,
                term,
                json.dumps(weeks, ensure_ascii=False),
                json.dumps(counts, ensure_ascii=False),
                json.dumps(source, ensure_ascii=False),
                agent,
                model,
                status,
                created_at,
            ),
        )
        conn.executemany(
            """
            INSERT INTO quiz_questions
                (id, set_id, ordinal, qtype, question, choices_json, answer, rubric, explanation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    q.id,
                    set_id,
                    q.ordinal,
                    q.qtype,
                    q.question,
                    json.dumps(q.choices, ensure_ascii=False),
                    q.answer,
                    q.rubric,
                    q.explanation,
                )
                for q in questions
            ],
        )

    return QuizSet(
        id=set_id,
        course_id=course_id,
        course_name=course_name,
        term=term,
        weeks=list(weeks),
        counts=dict(counts),
        source=dict(source),
        agent=agent,
        model=model,
        status=status,
        created_at=created_at,
        questions=questions,
    )


def _row_to_question(row: Any) -> QuizQuestion:
    try:
        choices = json.loads(row["choices_json"] or "[]")
    except (json.JSONDecodeError, TypeError):
        choices = []
    return QuizQuestion(
        id=row["id"],
        ordinal=row["ordinal"],
        qtype=row["qtype"],
        question=row["question"],
        choices=choices if isinstance(choices, list) else [],
        answer=row["answer"] or "",
        rubric=row["rubric"] or "",
        explanation=row["explanation"] or "",
    )


def _row_to_set(row: Any, questions: list[QuizQuestion]) -> QuizSet:
    def _loads(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except (json.JSONDecodeError, TypeError):
            return fallback

    return QuizSet(
        id=row["id"],
        course_id=row["course_id"],
        course_name=row["course_name"],
        term=row["term"] or "",
        weeks=_loads(row["weeks_json"], []),
        counts=_loads(row["counts_json"], {}),
        source=_loads(row["source_json"], {}),
        agent=row["agent"] or "",
        model=row["model"] or "",
        status=row["status"] or "ready",
        created_at=row["created_at"],
        questions=questions,
    )


def get_quiz_set(set_id: str) -> QuizSet | None:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM quiz_sets WHERE id = ?", (set_id,)).fetchone()
        if not row:
            return None
        q_rows = conn.execute("SELECT * FROM quiz_questions WHERE set_id = ? ORDER BY ordinal", (set_id,)).fetchall()
    return _row_to_set(row, [_row_to_question(r) for r in q_rows])


def list_quiz_sets(course_id: str | None = None, limit: int = 200) -> list[QuizSet]:
    with db._connect() as conn:
        if course_id:
            set_rows = conn.execute(
                "SELECT * FROM quiz_sets WHERE course_id = ? ORDER BY created_at DESC LIMIT ?",
                (course_id, limit),
            ).fetchall()
        else:
            set_rows = conn.execute("SELECT * FROM quiz_sets ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        if not set_rows:
            return []
        ids = [r["id"] for r in set_rows]
        placeholders = ",".join("?" * len(ids))
        q_rows = conn.execute(
            f"SELECT * FROM quiz_questions WHERE set_id IN ({placeholders}) ORDER BY ordinal",
            ids,
        ).fetchall()

    by_set: dict[str, list[QuizQuestion]] = {}
    for r in q_rows:
        by_set.setdefault(r["set_id"], []).append(_row_to_question(r))
    return [_row_to_set(r, by_set.get(r["id"], [])) for r in set_rows]


def delete_quiz_set(set_id: str) -> bool:
    with db._connect() as conn:
        cur = conn.execute("DELETE FROM quiz_sets WHERE id = ?", (set_id,))
        conn.execute("DELETE FROM quiz_questions WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM quiz_attempts WHERE set_id = ?", (set_id,))
        return cur.rowcount > 0


def create_attempt(
    *,
    set_id: str,
    answers: list[dict[str, Any]],
    results: list[GradeResult],
    score_total: float | None,
    graded_status: str,
) -> dict[str, Any]:
    attempt_id = _new_id()
    created_at = _now_iso()
    graded_at = created_at if graded_status in {"completed", "failed"} else None
    with db._connect() as conn:
        conn.execute(
            """
            INSERT INTO quiz_attempts
                (id, set_id, answers_json, results_json, score_total, graded_status, created_at, graded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                set_id,
                json.dumps(answers, ensure_ascii=False),
                json.dumps([r.to_dict() for r in results], ensure_ascii=False),
                score_total,
                graded_status,
                created_at,
                graded_at,
            ),
        )
    return get_attempt(attempt_id)  # type: ignore[return-value]


def update_attempt_results(
    attempt_id: str,
    *,
    results: list[GradeResult],
    score_total: float | None,
    graded_status: str,
) -> None:
    with db._connect() as conn:
        conn.execute(
            """
            UPDATE quiz_attempts
               SET results_json = ?, score_total = ?, graded_status = ?, graded_at = ?
             WHERE id = ?
            """,
            (
                json.dumps([r.to_dict() for r in results], ensure_ascii=False),
                score_total,
                graded_status,
                _now_iso(),
                attempt_id,
            ),
        )


def _row_to_attempt(row: Any) -> dict[str, Any]:
    def _loads(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value or "")
        except (json.JSONDecodeError, TypeError):
            return fallback

    return {
        "id": row["id"],
        "set_id": row["set_id"],
        "answers": _loads(row["answers_json"], []),
        "results": _loads(row["results_json"], []),
        "score_total": row["score_total"],
        "graded_status": row["graded_status"],
        "created_at": row["created_at"],
        "graded_at": row["graded_at"],
    }


def get_attempt(attempt_id: str) -> dict[str, Any] | None:
    with db._connect() as conn:
        row = conn.execute("SELECT * FROM quiz_attempts WHERE id = ?", (attempt_id,)).fetchone()
    return _row_to_attempt(row) if row else None


def list_attempts(set_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM quiz_attempts WHERE set_id = ? ORDER BY created_at DESC LIMIT ?",
            (set_id, limit),
        ).fetchall()
    return [_row_to_attempt(r) for r in rows]
