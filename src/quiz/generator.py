"""강의 자료 컨텍스트로부터 예상 문제를 생성한다.

AI 호출은 summarizer.generate_text()(gemini/openai/openrouter 분기)를 재사용한다.
요약 파이프라인과 달리 응답을 채점 가능한 구조로 강제해야 하므로, JSON 스키마를
프롬프트에 명시하고 파싱 실패 시 1회 재시도한다.
"""

from __future__ import annotations

import json
import re

from src.quiz.models import (
    MAX_COUNT_PER_TYPE,
    MULTIPLE_CHOICE_OPTIONS,
    QTYPE_ESSAY,
    QTYPE_MULTIPLE_CHOICE,
    QTYPE_SHORT_ANSWER,
    QuizQuestion,
)

# 입력 컨텍스트가 이 길이를 넘으면 유형별로 호출을 나눠 응답 토큰 초과를 피한다.
_SPLIT_CONTEXT_THRESHOLD = 40_000


class QuizGenerationError(RuntimeError):
    """LLM 응답을 문제 구조로 파싱하지 못했을 때."""


_SCHEMA_HINT = """\
반드시 아래 JSON 형식으로만 응답하세요. 코드블록, 설명, 인사말 없이 JSON 객체 하나만 출력합니다.

{
  "questions": [
    {
      "type": "multiple_choice",
      "question": "문제 지문",
      "choices": ["보기1", "보기2", "보기3", "보기4", "보기5"],
      "answer_index": 0,
      "explanation": "정답 해설"
    },
    {
      "type": "short_answer",
      "question": "문제 지문",
      "answer": "모범 답안(핵심 문구)",
      "rubric": "채점 포인트 / 인정 가능한 키워드",
      "explanation": "해설"
    },
    {
      "type": "essay",
      "question": "서술형 문제 지문",
      "rubric": "평가 기준 3~4개 항목을 줄바꿈으로",
      "explanation": "모범 답안 개요"
    }
  ]
}

규칙:
- 반드시 제공된 강의 자료에 근거해 출제하고, 자료에 없는 내용은 만들지 마세요.
- multiple_choice의 choices는 정확히 5개, answer_index는 0~4.
- 한국어로 출제하세요.
"""


def _build_prompt(context: str, counts: dict[str, int], extra: str = "") -> str:
    want = []
    if counts.get(QTYPE_MULTIPLE_CHOICE):
        want.append(f"- 객관식(multiple_choice) {counts[QTYPE_MULTIPLE_CHOICE]}문제")
    if counts.get(QTYPE_SHORT_ANSWER):
        want.append(f"- 단답형(short_answer) {counts[QTYPE_SHORT_ANSWER]}문제")
    if counts.get(QTYPE_ESSAY):
        want.append(f"- 서술형(essay) {counts[QTYPE_ESSAY]}문제")
    want_block = "\n".join(want)
    return (
        "당신은 대학 시험 예상 문제를 출제하는 조교입니다.\n"
        "아래 강의 자료를 바탕으로 다음 개수만큼 문제를 출제하세요.\n\n"
        f"{want_block}\n\n"
        f"{_SCHEMA_HINT}\n"
        f"{extra}\n\n"
        "===== 강의 자료 시작 =====\n"
        f"{context}\n"
        "===== 강의 자료 끝 =====\n"
    )


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_json_object(text: str) -> dict:
    candidate = _strip_code_fence(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # 앞뒤 잡텍스트가 붙은 경우 첫 '{' ~ 마지막 '}' 구간을 시도.
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as e:
            raise QuizGenerationError(f"JSON 파싱 실패: {e}") from e
    raise QuizGenerationError("응답에서 JSON 객체를 찾지 못했습니다.")


def _coerce_questions(payload: dict, allowed_types: set[str]) -> list[QuizQuestion]:
    raw_list = payload.get("questions")
    if not isinstance(raw_list, list):
        raise QuizGenerationError("'questions' 배열이 없습니다.")

    questions: list[QuizQuestion] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        qtype = str(raw.get("type") or "").strip()
        if qtype not in allowed_types:
            continue
        question = str(raw.get("question") or "").strip()
        if not question:
            continue

        if qtype == QTYPE_MULTIPLE_CHOICE:
            choices = [str(c).strip() for c in raw.get("choices") or [] if str(c).strip()]
            if len(choices) < 2:
                continue
            choices = choices[:MULTIPLE_CHOICE_OPTIONS]
            try:
                answer_index = int(raw.get("answer_index", 0))
            except (TypeError, ValueError):
                answer_index = 0
            answer_index = max(0, min(len(choices) - 1, answer_index))
            questions.append(
                QuizQuestion(
                    qtype=qtype,
                    question=question,
                    choices=choices,
                    answer=str(answer_index),
                    explanation=str(raw.get("explanation") or "").strip(),
                )
            )
        else:
            questions.append(
                QuizQuestion(
                    qtype=qtype,
                    question=question,
                    answer=str(raw.get("answer") or "").strip(),
                    rubric=str(raw.get("rubric") or "").strip(),
                    explanation=str(raw.get("explanation") or "").strip(),
                )
            )
    return questions


def _generate_once(*, agent: str, api_key: str, model: str, context: str, counts: dict[str, int]) -> list[QuizQuestion]:
    from src.summarizer.summarizer import generate_text

    allowed = {qt for qt, n in counts.items() if n > 0}
    prompt = _build_prompt(context, counts)
    response = generate_text(agent, api_key, model, prompt)
    try:
        payload = _extract_json_object(response)
        questions = _coerce_questions(payload, allowed)
    except QuizGenerationError:
        # 재시도 — JSON만 출력하도록 재강조.
        retry_prompt = _build_prompt(
            context, counts, extra="\n[중요] 앞선 응답이 잘못되었습니다. 오직 JSON 객체만 출력하세요."
        )
        response = generate_text(agent, api_key, model, retry_prompt)
        payload = _extract_json_object(response)
        questions = _coerce_questions(payload, allowed)

    if not questions:
        raise QuizGenerationError("생성된 문제가 없습니다. 자료가 부족하거나 응답 형식이 올바르지 않습니다.")
    return questions


def generate_quiz(
    *,
    context: str,
    counts: dict[str, int],
    agent: str,
    api_key: str,
    model: str,
) -> list[QuizQuestion]:
    """컨텍스트와 유형별 개수로 문제를 생성한다.

    counts: {"multiple_choice": n, "short_answer": n, "essay": n} — 각 0..20.
    반환된 문제 수는 요청보다 적을 수 있다(자료 부족 시).
    """
    if not context.strip():
        raise QuizGenerationError("문제를 생성할 강의 자료가 비어 있습니다.")

    counts = {k: max(0, min(MAX_COUNT_PER_TYPE, int(v))) for k, v in counts.items()}
    total = sum(counts.values())
    if total <= 0:
        raise QuizGenerationError("생성할 문제 수가 0입니다.")

    active_types = [qt for qt, n in counts.items() if n > 0]
    # 컨텍스트가 크고 유형이 여러 개면 유형별로 나눠 호출한다.
    if len(context) > _SPLIT_CONTEXT_THRESHOLD and len(active_types) > 1:
        collected: list[QuizQuestion] = []
        for qt in active_types:
            collected.extend(
                _generate_once(
                    agent=agent,
                    api_key=api_key,
                    model=model,
                    context=context,
                    counts={qt: counts[qt]},
                )
            )
        questions = collected
    else:
        questions = _generate_once(agent=agent, api_key=api_key, model=model, context=context, counts=counts)

    # 유형별 요청 개수로 자른다.
    remaining = dict(counts)
    trimmed: list[QuizQuestion] = []
    for q in questions:
        if remaining.get(q.qtype, 0) > 0:
            trimmed.append(q)
            remaining[q.qtype] -= 1
    return trimmed
