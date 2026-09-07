"""src/quiz/grader.py 단위 테스트."""

import json
from unittest.mock import patch

from src.quiz.grader import grade_attempt, grade_multiple_choice, grade_subjective, score_total
from src.quiz.models import (
    QTYPE_ESSAY,
    QTYPE_MULTIPLE_CHOICE,
    QTYPE_SHORT_ANSWER,
    QuizQuestion,
)


def _mc(qid="q1", answer="2"):
    return QuizQuestion(
        id=qid,
        qtype=QTYPE_MULTIPLE_CHOICE,
        question="?",
        choices=["a", "b", "c"],
        answer=answer,
        explanation="정답은 c",
    )


def test_grade_multiple_choice_correct():
    r = grade_multiple_choice(_mc(), "2")
    assert r.correct is True
    assert r.score == 100.0


def test_grade_multiple_choice_wrong_includes_explanation():
    r = grade_multiple_choice(_mc(), "0")
    assert r.correct is False
    assert r.score == 0.0
    assert "정답은 c" in r.feedback


def test_grade_attempt_only_mc_completes_without_ai():
    questions = [_mc("q1", "1"), _mc("q2", "0")]
    results, status = grade_attempt(questions=questions, user_answers={"q1": "1", "q2": "2"}, grade_subjective_now=True)
    assert status == "completed"
    assert results[0].correct is True
    assert results[1].correct is False
    assert score_total(results) == 50.0


def test_grade_attempt_with_subjective_partial_when_deferred():
    questions = [_mc("q1", "1"), QuizQuestion(id="q2", qtype=QTYPE_SHORT_ANSWER, question="?", answer="서울")]
    results, status = grade_attempt(
        questions=questions, user_answers={"q1": "1", "q2": "서울"}, grade_subjective_now=False
    )
    assert status == "partial"
    assert len(results) == 1  # 객관식만


def test_grade_subjective_calls_llm_once_and_parses():
    questions = [
        QuizQuestion(id="s1", qtype=QTYPE_SHORT_ANSWER, question="수도?", answer="서울", rubric="서울"),
        QuizQuestion(id="s2", qtype=QTYPE_ESSAY, question="논하시오", rubric="기준"),
    ]
    payload = {
        "results": [
            {"index": 0, "score": 100, "correct": True, "feedback": "정답"},
            {"index": 1, "score": 70, "correct": True, "feedback": "대체로 좋음"},
        ]
    }
    with patch("src.summarizer.summarizer.generate_text", return_value=json.dumps(payload)) as gt:
        results = grade_subjective(
            questions=questions,
            user_answers={"s1": "서울", "s2": "긴 답안"},
            agent="gemini",
            api_key="k",
            model="m",
        )
    assert gt.call_count == 1
    assert results[0].score == 100.0
    assert results[1].score == 70.0
    assert results[1].question_id == "s2"


def test_grade_subjective_no_subjective_returns_empty():
    assert grade_subjective(questions=[_mc()], user_answers={}, agent="g", api_key="k", model="m") == []
