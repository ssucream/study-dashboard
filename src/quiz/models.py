"""예상 문제 기능의 데이터 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 문제 유형 — DB(qtype), API, 프론트에서 공통으로 쓰는 문자열 값.
QTYPE_MULTIPLE_CHOICE = "multiple_choice"
QTYPE_SHORT_ANSWER = "short_answer"
QTYPE_ESSAY = "essay"

QTYPES = (QTYPE_MULTIPLE_CHOICE, QTYPE_SHORT_ANSWER, QTYPE_ESSAY)
SUBJECTIVE_QTYPES = (QTYPE_SHORT_ANSWER, QTYPE_ESSAY)

# 유형별 최대 문항 수 (요구사항: 각 20문제).
MAX_COUNT_PER_TYPE = 20

# 객관식 보기 개수 (5지선다 1택).
MULTIPLE_CHOICE_OPTIONS = 5


@dataclass
class QuizQuestion:
    """문제 한 건. 생성 시점에 정답·해설·채점기준까지 확정해 저장한다."""

    qtype: str
    question: str
    ordinal: int = 0
    choices: list[str] = field(default_factory=list)  # 객관식 보기 5개
    answer: str = ""  # 객관식: 정답 인덱스(문자열), 단답형: 모범답안
    rubric: str = ""  # 주관식 채점 기준/포인트
    explanation: str = ""  # 해설
    id: str = ""

    def is_subjective(self) -> bool:
        return self.qtype in SUBJECTIVE_QTYPES

    def public_dict(self) -> dict[str, Any]:
        """풀이 화면용 — 정답/해설/채점기준을 제외한다."""
        data: dict[str, Any] = {
            "id": self.id,
            "ordinal": self.ordinal,
            "qtype": self.qtype,
            "question": self.question,
        }
        if self.qtype == QTYPE_MULTIPLE_CHOICE:
            data["choices"] = list(self.choices)
        return data

    def full_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ordinal": self.ordinal,
            "qtype": self.qtype,
            "question": self.question,
            "choices": list(self.choices),
            "answer": self.answer,
            "rubric": self.rubric,
            "explanation": self.explanation,
        }


@dataclass
class QuizSet:
    """문제셋 메타데이터."""

    id: str
    course_id: str
    course_name: str
    term: str = ""
    weeks: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)
    agent: str = ""
    model: str = ""
    status: str = "ready"  # ready / failed
    created_at: str = ""
    questions: list[QuizQuestion] = field(default_factory=list)

    def summary_dict(self) -> dict[str, Any]:
        """목록 화면용 요약."""
        by_type = {qt: 0 for qt in QTYPES}
        for q in self.questions:
            if q.qtype in by_type:
                by_type[q.qtype] += 1
        return {
            "id": self.id,
            "course_id": self.course_id,
            "course_name": self.course_name,
            "term": self.term,
            "weeks": list(self.weeks),
            "counts": dict(self.counts),
            "question_count": len(self.questions),
            "question_counts_by_type": by_type,
            "status": self.status,
            "created_at": self.created_at,
        }


@dataclass
class GradeResult:
    """문항 한 건의 채점 결과."""

    question_id: str
    correct: bool
    score: float  # 0~100
    feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "correct": self.correct,
            "score": self.score,
            "feedback": self.feedback,
        }


def normalize_counts(raw: dict[str, Any] | None) -> dict[str, int]:
    """유형별 문항 수 요청을 0..MAX_COUNT_PER_TYPE 정수로 정규화한다."""
    raw = raw or {}
    counts: dict[str, int] = {}
    for qt in QTYPES:
        try:
            value = int(raw.get(qt, 0))
        except (TypeError, ValueError):
            value = 0
        counts[qt] = max(0, min(MAX_COUNT_PER_TYPE, value))
    return counts
