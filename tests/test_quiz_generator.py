"""src/quiz/generator.py 단위 테스트."""

import json
from unittest.mock import patch

import pytest

from src.quiz.generator import QuizGenerationError, generate_quiz
from src.quiz.models import QTYPE_ESSAY, QTYPE_MULTIPLE_CHOICE, QTYPE_SHORT_ANSWER

_VALID_PAYLOAD = {
    "questions": [
        {
            "type": "multiple_choice",
            "question": "2+2는?",
            "choices": ["1", "2", "3", "4", "5"],
            "answer_index": 3,
            "explanation": "4입니다.",
        },
        {
            "type": "short_answer",
            "question": "수도는?",
            "answer": "서울",
            "rubric": "서울 인정",
            "explanation": "대한민국 수도",
        },
        {
            "type": "essay",
            "question": "설명하시오.",
            "rubric": "기준1\n기준2",
            "explanation": "개요",
        },
    ]
}


def test_generate_quiz_parses_valid_json():
    with patch("src.summarizer.summarizer.generate_text", return_value=json.dumps(_VALID_PAYLOAD)):
        questions = generate_quiz(
            context="강의 자료",
            counts={QTYPE_MULTIPLE_CHOICE: 1, QTYPE_SHORT_ANSWER: 1, QTYPE_ESSAY: 1},
            agent="gemini",
            api_key="k",
            model="m",
        )
    assert len(questions) == 3
    mc = next(q for q in questions if q.qtype == QTYPE_MULTIPLE_CHOICE)
    assert mc.choices == ["1", "2", "3", "4", "5"]
    assert mc.answer == "3"


def test_generate_quiz_strips_code_fence():
    fenced = "```json\n" + json.dumps(_VALID_PAYLOAD) + "\n```"
    with patch("src.summarizer.summarizer.generate_text", return_value=fenced):
        questions = generate_quiz(
            context="자료",
            counts={QTYPE_MULTIPLE_CHOICE: 1},
            agent="gemini",
            api_key="k",
            model="m",
        )
    assert len(questions) == 1


def test_generate_quiz_retries_once_on_bad_json():
    responses = iter(["설명: 문제를 만들었습니다 (JSON 아님)", json.dumps(_VALID_PAYLOAD)])
    with patch("src.summarizer.summarizer.generate_text", side_effect=lambda *a, **k: next(responses)):
        questions = generate_quiz(
            context="자료",
            counts={QTYPE_SHORT_ANSWER: 1},
            agent="gemini",
            api_key="k",
            model="m",
        )
    assert len(questions) == 1
    assert questions[0].qtype == QTYPE_SHORT_ANSWER


def test_generate_quiz_raises_after_second_failure():
    with patch("src.summarizer.summarizer.generate_text", return_value="여전히 JSON 아님"):
        with pytest.raises(QuizGenerationError):
            generate_quiz(
                context="자료",
                counts={QTYPE_SHORT_ANSWER: 1},
                agent="gemini",
                api_key="k",
                model="m",
            )


def test_generate_quiz_trims_to_requested_counts():
    payload = {"questions": [_VALID_PAYLOAD["questions"][0]] * 5}
    with patch("src.summarizer.summarizer.generate_text", return_value=json.dumps(payload)):
        questions = generate_quiz(
            context="자료",
            counts={QTYPE_MULTIPLE_CHOICE: 2},
            agent="gemini",
            api_key="k",
            model="m",
        )
    assert len(questions) == 2


def test_generate_quiz_empty_context_raises():
    with pytest.raises(QuizGenerationError):
        generate_quiz(context="   ", counts={QTYPE_MULTIPLE_CHOICE: 1}, agent="gemini", api_key="k", model="m")
