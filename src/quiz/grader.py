"""사용자 답안 채점.

객관식은 서버가 정답을 알고 있으므로 로컬에서 즉시 채점한다(AI 호출 없음).
단답형/서술형은 의미 판단이 필요하므로, 문제+채점기준+사용자 답안을 배치로 묶어
LLM에 1회 호출한다.
"""

from __future__ import annotations

import json
import re

from src.quiz.models import QTYPE_MULTIPLE_CHOICE, GradeResult, QuizQuestion


class QuizGradingError(RuntimeError):
    pass


def grade_multiple_choice(question: QuizQuestion, user_answer: str) -> GradeResult:
    """객관식 로컬 채점. user_answer는 선택한 보기 인덱스(문자열)."""
    try:
        picked = int(str(user_answer).strip())
    except (TypeError, ValueError):
        picked = -1
    try:
        correct_index = int(question.answer)
    except (TypeError, ValueError):
        correct_index = 0
    is_correct = picked == correct_index
    return GradeResult(
        question_id=question.id,
        correct=is_correct,
        score=100.0 if is_correct else 0.0,
        feedback="" if is_correct else question.explanation,
    )


_GRADE_SCHEMA_HINT = """\
아래 JSON 형식으로만 응답하세요. 코드블록·설명 없이 JSON 객체 하나만 출력합니다.

{
  "results": [
    {"index": 0, "score": 0-100 정수, "correct": true/false, "feedback": "채점 사유와 보완점"}
  ]
}

- score는 0~100. 단답형은 대체로 맞으면 100, 틀리면 0, 부분 인정 시 중간값.
- 서술형은 채점 기준 충족도에 따라 부분 점수를 매기세요.
- feedback에는 왜 그 점수인지, 무엇이 빠졌는지 한국어로 간단히 적으세요.
"""


def _build_grade_prompt(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items):
        lines.append(f"[문제 {i}] ({it['qtype']})")
        lines.append(f"지문: {it['question']}")
        if it.get("model_answer"):
            lines.append(f"모범답안: {it['model_answer']}")
        if it.get("rubric"):
            lines.append(f"채점기준: {it['rubric']}")
        lines.append(f"학생답안: {it['user_answer'] or '(무응답)'}")
        lines.append("")
    return (
        "당신은 대학 시험 채점자입니다. 각 문제의 채점기준에 따라 학생 답안을 채점하세요.\n\n"
        f"{_GRADE_SCHEMA_HINT}\n\n"
        "===== 채점 대상 =====\n" + "\n".join(lines)
    )


def _extract_json_object(text: str) -> dict:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            raise QuizGradingError(f"채점 응답 JSON 파싱 실패: {e}") from e
    raise QuizGradingError("채점 응답에서 JSON을 찾지 못했습니다.")


def grade_subjective(
    *,
    questions: list[QuizQuestion],
    user_answers: dict[str, str],
    agent: str,
    api_key: str,
    model: str,
) -> list[GradeResult]:
    """단답형/서술형 문항을 LLM 1회 호출로 채점한다."""
    subjective = [q for q in questions if q.qtype != QTYPE_MULTIPLE_CHOICE]
    if not subjective:
        return []

    items = [
        {
            "qtype": q.qtype,
            "question": q.question,
            "model_answer": q.answer,
            "rubric": q.rubric,
            "user_answer": user_answers.get(q.id, ""),
        }
        for q in subjective
    ]

    from src.summarizer.summarizer import generate_text

    prompt = _build_grade_prompt(items)
    response = generate_text(agent, api_key, model, prompt)
    try:
        payload = _extract_json_object(response)
    except QuizGradingError:
        response = generate_text(agent, api_key, model, prompt + "\n\n[중요] 오직 JSON 객체만 출력하세요.")
        payload = _extract_json_object(response)

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        raise QuizGradingError("채점 응답에 'results' 배열이 없습니다.")

    by_index: dict[int, dict] = {}
    for r in raw_results:
        if isinstance(r, dict) and isinstance(r.get("index"), int):
            by_index[r["index"]] = r

    results: list[GradeResult] = []
    for i, q in enumerate(subjective):
        r = by_index.get(i, {})
        try:
            score = float(r.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(100.0, score))
        correct = bool(r.get("correct")) if "correct" in r else score >= 60.0
        results.append(
            GradeResult(
                question_id=q.id,
                correct=correct,
                score=score,
                feedback=str(r.get("feedback") or "").strip(),
            )
        )
    return results


def grade_attempt(
    *,
    questions: list[QuizQuestion],
    user_answers: dict[str, str],
    agent: str = "",
    api_key: str = "",
    model: str = "",
    grade_subjective_now: bool = True,
) -> tuple[list[GradeResult], str]:
    """전체 답안을 채점한다.

    반환: (results, graded_status)
      - grade_subjective_now=False 또는 주관식이 없으면 객관식만 채점하고 "partial"
      - 주관식까지 채점하면 "completed"
    """
    results: list[GradeResult] = []
    has_subjective = False
    for q in questions:
        if q.qtype == QTYPE_MULTIPLE_CHOICE:
            results.append(grade_multiple_choice(q, user_answers.get(q.id, "")))
        else:
            has_subjective = True

    if not has_subjective:
        return results, "completed"

    if not grade_subjective_now:
        return results, "partial"

    results.extend(
        grade_subjective(
            questions=questions,
            user_answers=user_answers,
            agent=agent,
            api_key=api_key,
            model=model,
        )
    )
    return results, "completed"


def score_total(results: list[GradeResult]) -> float | None:
    """0~100 환산 총점 (문항 균등 가중)."""
    if not results:
        return None
    return round(sum(r.score for r in results) / len(results), 1)
