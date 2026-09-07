"""src/quiz/store.py — SQLite 저장소 테스트."""

import pytest

from src.quiz import store
from src.quiz.models import QTYPE_MULTIPLE_CHOICE, QTYPE_SHORT_ANSWER, GradeResult, QuizQuestion


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_schema_ready_paths", set())
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    yield


def _questions():
    return [
        QuizQuestion(
            qtype=QTYPE_MULTIPLE_CHOICE, question="1+1?", choices=["1", "2", "3", "4", "5"], answer="1", explanation="2"
        ),
        QuizQuestion(qtype=QTYPE_SHORT_ANSWER, question="수도?", answer="서울", rubric="서울 인정"),
    ]


def _create():
    return store.create_quiz_set(
        course_id="42",
        course_name="테스트 과목",
        term="2026-1",
        weeks=["1주차", "2주차"],
        counts={QTYPE_MULTIPLE_CHOICE: 1, QTYPE_SHORT_ANSWER: 1},
        source={"context_chars": 123},
        agent="gemini",
        model="gemini-x",
        questions=_questions(),
    )


def test_create_and_get_quiz_set():
    created = _create()
    fetched = store.get_quiz_set(created.id)
    assert fetched is not None
    assert fetched.course_name == "테스트 과목"
    assert len(fetched.questions) == 2
    assert fetched.questions[0].ordinal == 0
    assert fetched.questions[0].choices == ["1", "2", "3", "4", "5"]
    assert fetched.questions[1].answer == "서울"


def test_list_quiz_sets_filters_by_course():
    _create()
    assert len(store.list_quiz_sets()) == 1
    assert len(store.list_quiz_sets(course_id="42")) == 1
    assert store.list_quiz_sets(course_id="99") == []


def test_delete_quiz_set_cascades():
    created = _create()
    attempt = store.create_attempt(
        set_id=created.id,
        answers=[{"question_id": created.questions[0].id, "answer": "1"}],
        results=[GradeResult(question_id=created.questions[0].id, correct=True, score=100.0)],
        score_total=100.0,
        graded_status="completed",
    )
    assert store.get_attempt(attempt["id"]) is not None

    assert store.delete_quiz_set(created.id) is True
    assert store.get_quiz_set(created.id) is None
    assert store.list_attempts(created.id) == []
    assert store.delete_quiz_set(created.id) is False


def test_attempt_update_results():
    created = _create()
    qid = created.questions[1].id
    attempt = store.create_attempt(
        set_id=created.id,
        answers=[{"question_id": qid, "answer": "서울"}],
        results=[],
        score_total=None,
        graded_status="partial",
    )
    store.update_attempt_results(
        attempt["id"],
        results=[GradeResult(question_id=qid, correct=True, score=90.0, feedback="좋음")],
        score_total=90.0,
        graded_status="completed",
    )
    updated = store.get_attempt(attempt["id"])
    assert updated["graded_status"] == "completed"
    assert updated["score_total"] == 90.0
    assert updated["results"][0]["feedback"] == "좋음"
    assert updated["graded_at"] is not None
