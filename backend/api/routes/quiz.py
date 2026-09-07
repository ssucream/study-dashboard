"""예상 문제 생성·풀이·채점 API."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

from backend.api.auth_dep import require_auth
from backend.api.state import app_state
from backend.api.task_manager import ManagedTask, task_manager
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from src import event_log
from src.config import Config
from src.quiz import material_store, store
from src.quiz.generator import QuizGenerationError, generate_quiz
from src.quiz.grader import QuizGradingError, grade_attempt, grade_subjective, score_total
from src.quiz.materials import MAX_MATERIAL_BYTES, MaterialExtractionError, extract_text, is_supported_filename
from src.quiz.models import QTYPE_MULTIPLE_CHOICE, normalize_counts
from src.quiz.source_collector import MaterialText, build_context, gather_lecture_texts

router = APIRouter()


def _find_course_index(course_id: str) -> int | None:
    return next((i for i, c in enumerate(app_state.courses) if c.id == course_id), None)


def _require_ai_ready() -> None:
    if Config.AI_ENABLED != "true":
        raise HTTPException(status_code=409, detail="설정에서 AI 기능을 먼저 활성화하세요.")
    if not Config.get_ai_api_key():
        raise HTTPException(status_code=409, detail="AI API 키가 설정되어 있지 않습니다.")
    if not Config.get_ai_model():
        raise HTTPException(status_code=409, detail="AI 모델이 설정되어 있지 않습니다.")


def _course_ctx(course_id: str):
    idx = _find_course_index(course_id)
    if idx is None:
        raise HTTPException(status_code=404, detail="과목을 찾을 수 없습니다.")
    course = app_state.courses[idx]
    detail = app_state.details[idx] if idx < len(app_state.details) else None
    if not detail:
        raise HTTPException(status_code=404, detail="강의 정보를 불러오지 못했습니다.")
    return course, detail


# ─────────────────────────────────────────────────────────────
# 강의자료 업로드
# ─────────────────────────────────────────────────────────────
@router.post("/materials")
async def upload_material(
    course_id: str = Form(...),
    week_label: str = Form(""),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    require_auth()
    course, _detail = _course_ctx(course_id)

    filename = file.filename or ""
    if not is_supported_filename(filename):
        raise HTTPException(status_code=400, detail="PDF / PPTX / DOCX 파일만 업로드할 수 있습니다.")

    data = await file.read()
    if len(data) > MAX_MATERIAL_BYTES:
        raise HTTPException(
            status_code=413, detail=f"파일이 너무 큽니다. 최대 {MAX_MATERIAL_BYTES // (1024 * 1024)}MB."
        )

    # 텍스트 추출을 먼저 시도해 손상 파일을 걸러낸다(빈 텍스트는 허용, 경고만).
    try:
        text = await asyncio.get_running_loop().run_in_executor(None, extract_text, filename, data)
    except MaterialExtractionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    meta = material_store.save_material(
        download_dir=Config.get_download_dir(),
        course_name=course.long_name,
        week_label=week_label,
        filename=filename,
        data=data,
    )
    meta["text_chars"] = len(text)
    meta["text_empty"] = len(text.strip()) == 0
    event_log.record_event(
        event_type="quiz",
        action="material_upload",
        status="success",
        actor_user_id=app_state.user_id or None,
        course_id=course_id,
        course_name=course.long_name,
        week_label=week_label,
        message=f"강의자료 업로드: {meta['name']}",
        metadata={"material_id": meta["id"], "text_chars": meta["text_chars"]},
    )
    return meta


@router.get("/materials")
async def list_materials(course_id: str | None = None) -> dict[str, Any]:
    require_auth()
    course_name = None
    if course_id:
        course, _ = _course_ctx(course_id)
        course_name = course.long_name
    return {"materials": material_store.list_materials(download_dir=Config.get_download_dir(), course_name=course_name)}


@router.delete("/materials/{material_id}")
async def delete_material(material_id: str) -> dict[str, Any]:
    require_auth()
    try:
        deleted = material_store.delete_material(material_id, Config.get_download_dir())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not deleted:
        raise HTTPException(status_code=404, detail="자료를 찾을 수 없습니다.")
    return {"deleted": True}


# ─────────────────────────────────────────────────────────────
# 문제셋 생성
# ─────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    course_id: str
    week_numbers: list[int] = []
    material_ids: list[str] = []
    counts: dict[str, int] = {}
    include_stt: bool = True


def _lectures_for_weeks(detail, week_numbers: list[int]) -> tuple[list[str], list[tuple[str, str]]]:
    """선택된 주차의 (표시용 주차 라벨 목록, [(week_label, lecture_title)]) 를 반환한다.

    week_numbers가 비어 있으면(자료만으로 생성) 강의 텍스트는 수집하지 않는다.
    """
    wanted = set(week_numbers)
    if not wanted:
        return [], []
    week_labels: list[str] = []
    lectures: list[tuple[str, str]] = []
    for week in detail.weeks:
        if week.week_number not in wanted:
            continue
        week_labels.append(week.title)
        for lec in week.lectures:
            if lec.is_video:
                lectures.append((lec.week_label or week.title, lec.title))
    return week_labels, lectures


@router.post("")
async def generate_quiz_set(req: GenerateRequest) -> dict[str, Any]:
    require_auth()
    _require_ai_ready()
    course, detail = _course_ctx(req.course_id)

    counts = normalize_counts(req.counts)
    if sum(counts.values()) == 0:
        raise HTTPException(status_code=400, detail="생성할 문제 유형과 개수를 1개 이상 선택하세요.")

    week_labels, lectures = _lectures_for_weeks(detail, req.week_numbers)
    if not lectures and not req.material_ids:
        raise HTTPException(status_code=400, detail="시험 범위 주차나 업로드 자료를 1개 이상 선택하세요.")

    download_dir = Config.get_download_dir()
    material_paths = []
    for mid in req.material_ids:
        try:
            material_paths.append(material_store.get_material_path(mid, download_dir))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=400, detail=f"자료를 찾을 수 없습니다: {e}") from e

    agent = Config.AI_AGENT or "gemini"
    api_key = Config.get_ai_api_key()
    model = Config.get_ai_model()
    term = course.term
    course_name = course.long_name
    course_id = req.course_id
    include_stt = req.include_stt

    async def run(managed: ManagedTask) -> dict[str, Any]:
        managed.update(stage="collecting", message="강의 자료를 수집하는 중입니다.", progress_pct=15)
        loop = asyncio.get_running_loop()

        lecture_texts = await loop.run_in_executor(
            None,
            partial(
                gather_lecture_texts,
                term=term,
                course_name=course_name,
                download_dir=download_dir,
                lectures=lectures,
            ),
        )

        materials: list[MaterialText] = []
        for path in material_paths:
            try:
                text = await loop.run_in_executor(None, partial(extract_text, path.name, path.read_bytes()))
                materials.append(MaterialText(name=path.name, text=text))
            except MaterialExtractionError:
                continue

        ctx = build_context(
            week_labels=week_labels,
            lecture_texts=lecture_texts,
            materials=materials,
            include_stt=include_stt,
        )
        if not ctx.context.strip():
            raise QuizGenerationError(
                "문제를 생성할 자료가 없습니다. 선택한 주차에 요약본/전사본이 없거나 자료 텍스트가 비어 있습니다."
            )

        managed.update(stage="generating", message="AI가 문제를 생성하는 중입니다.", progress_pct=55)
        questions = await loop.run_in_executor(
            None,
            partial(
                generate_quiz,
                context=ctx.context,
                counts=counts,
                agent=agent,
                api_key=api_key,
                model=model,
            ),
        )

        quiz_set = store.create_quiz_set(
            course_id=course_id,
            course_name=course_name,
            term=term,
            weeks=week_labels,
            counts=counts,
            source={**ctx.stats, "material_ids": req.material_ids, "include_stt": include_stt},
            agent=agent,
            model=model,
            questions=questions,
        )
        event_log.record_event(
            event_type="quiz",
            action="generate_complete",
            status="success",
            actor_user_id=app_state.user_id or None,
            course_id=course_id,
            course_name=course_name,
            message=f"예상 문제 {len(questions)}개 생성 완료",
            metadata={"task_id": managed.id, "set_id": quiz_set.id, "counts": counts},
        )
        return {"set_id": quiz_set.id, "question_count": len(questions), "stats": ctx.stats}

    managed = task_manager.create(
        "quiz_generate",
        run,
        metadata={"course_id": course_id, "course_name": course_name, "counts": counts},
    )
    event_log.record_event(
        event_type="quiz",
        action="generate_start",
        status="started",
        actor_user_id=app_state.user_id or None,
        course_id=course_id,
        course_name=course_name,
        message="예상 문제 생성을 시작했습니다.",
        metadata={"task_id": managed.id, "counts": counts, "weeks": week_labels},
    )
    return {"started": True, "task_id": managed.id}


@router.get("")
async def list_quiz_sets(course_id: str | None = None) -> dict[str, Any]:
    require_auth()
    sets = store.list_quiz_sets(course_id=course_id)
    return {"quiz_sets": [s.summary_dict() for s in sets]}


@router.get("/{set_id}")
async def get_quiz_set(set_id: str) -> dict[str, Any]:
    require_auth()
    quiz_set = store.get_quiz_set(set_id)
    if not quiz_set:
        raise HTTPException(status_code=404, detail="문제셋을 찾을 수 없습니다.")
    payload = quiz_set.summary_dict()
    payload["questions"] = [q.public_dict() for q in quiz_set.questions]
    return payload


@router.delete("/{set_id}")
async def delete_quiz_set(set_id: str) -> dict[str, Any]:
    require_auth()
    if not store.delete_quiz_set(set_id):
        raise HTTPException(status_code=404, detail="문제셋을 찾을 수 없습니다.")
    return {"deleted": True}


# ─────────────────────────────────────────────────────────────
# 풀이 & 채점
# ─────────────────────────────────────────────────────────────
class AttemptAnswer(BaseModel):
    question_id: str
    answer: str = ""


class SubmitAttemptRequest(BaseModel):
    answers: list[AttemptAnswer] = []


def _result_payload(quiz_set, attempt: dict[str, Any]) -> dict[str, Any]:
    """채점 결과에 문항 본문·정답·해설을 합쳐 프론트로 내려준다."""
    q_by_id = {q.id: q for q in quiz_set.questions}
    results = []
    for r in attempt["results"]:
        q = q_by_id.get(r["question_id"])
        if not q:
            continue
        results.append(
            {
                **r,
                "qtype": q.qtype,
                "question": q.question,
                "choices": list(q.choices),
                "correct_answer": q.answer,
                "explanation": q.explanation,
            }
        )
    return {
        "id": attempt["id"],
        "set_id": attempt["set_id"],
        "graded_status": attempt["graded_status"],
        "score_total": attempt["score_total"],
        "created_at": attempt["created_at"],
        "graded_at": attempt["graded_at"],
        "answers": attempt["answers"],
        "results": results,
    }


@router.post("/{set_id}/attempts")
async def submit_attempt(set_id: str, req: SubmitAttemptRequest) -> dict[str, Any]:
    require_auth()
    quiz_set = store.get_quiz_set(set_id)
    if not quiz_set:
        raise HTTPException(status_code=404, detail="문제셋을 찾을 수 없습니다.")

    user_answers = {a.question_id: a.answer for a in req.answers}
    answers_list = [{"question_id": qid, "answer": ans} for qid, ans in user_answers.items()]
    has_subjective = any(q.qtype != QTYPE_MULTIPLE_CHOICE for q in quiz_set.questions)

    # 객관식 즉시 채점.
    results, graded_status = grade_attempt(
        questions=quiz_set.questions, user_answers=user_answers, grade_subjective_now=False
    )
    attempt = store.create_attempt(
        set_id=set_id,
        answers=answers_list,
        results=results,
        score_total=score_total(results) if not has_subjective else None,
        graded_status=graded_status,
    )

    if not has_subjective:
        event_log.record_event(
            event_type="quiz",
            action="grade_complete",
            status="success",
            actor_user_id=app_state.user_id or None,
            course_id=quiz_set.course_id,
            course_name=quiz_set.course_name,
            message="객관식 채점 완료",
            metadata={"set_id": set_id, "attempt_id": attempt["id"]},
        )
        return {"attempt_id": attempt["id"], "graded_status": "completed", "task_id": None}

    # 주관식 — AI 채점 백그라운드 Task.
    _require_ai_ready()
    agent = Config.AI_AGENT or "gemini"
    api_key = Config.get_ai_api_key()
    model = Config.get_ai_model()
    attempt_id = attempt["id"]
    questions = quiz_set.questions
    course_id = quiz_set.course_id
    course_name = quiz_set.course_name

    async def run(managed: ManagedTask) -> dict[str, Any]:
        managed.update(stage="grading", message="AI가 주관식을 채점하는 중입니다.", progress_pct=40)
        loop = asyncio.get_running_loop()
        try:
            subjective_results = await loop.run_in_executor(
                None,
                partial(
                    grade_subjective,
                    questions=questions,
                    user_answers=user_answers,
                    agent=agent,
                    api_key=api_key,
                    model=model,
                ),
            )
        except QuizGradingError as e:
            store.update_attempt_results(
                attempt_id, results=results, score_total=score_total(results), graded_status="failed"
            )
            raise RuntimeError(f"주관식 채점 실패: {e}") from e

        all_results = results + subjective_results
        total = score_total(all_results)
        store.update_attempt_results(attempt_id, results=all_results, score_total=total, graded_status="completed")
        event_log.record_event(
            event_type="quiz",
            action="grade_complete",
            status="success",
            actor_user_id=app_state.user_id or None,
            course_id=course_id,
            course_name=course_name,
            message="주관식 채점 완료",
            metadata={"set_id": set_id, "attempt_id": attempt_id, "score_total": total},
        )
        return {"attempt_id": attempt_id, "score_total": total}

    managed = task_manager.create(
        "quiz_grade",
        run,
        metadata={"set_id": set_id, "attempt_id": attempt_id, "course_name": course_name},
    )
    return {"attempt_id": attempt_id, "graded_status": "partial", "task_id": managed.id}


@router.get("/{set_id}/attempts")
async def list_attempts(set_id: str) -> dict[str, Any]:
    require_auth()
    quiz_set = store.get_quiz_set(set_id)
    if not quiz_set:
        raise HTTPException(status_code=404, detail="문제셋을 찾을 수 없습니다.")
    return {"attempts": [_result_payload(quiz_set, a) for a in store.list_attempts(set_id)]}


@router.get("/{set_id}/attempts/{attempt_id}")
async def get_attempt(set_id: str, attempt_id: str) -> dict[str, Any]:
    require_auth()
    quiz_set = store.get_quiz_set(set_id)
    if not quiz_set:
        raise HTTPException(status_code=404, detail="문제셋을 찾을 수 없습니다.")
    attempt = store.get_attempt(attempt_id)
    if not attempt or attempt["set_id"] != set_id:
        raise HTTPException(status_code=404, detail="채점 결과를 찾을 수 없습니다.")
    return _result_payload(quiz_set, attempt)
