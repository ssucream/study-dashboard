"""예상 문제 기능 웹 라우트 테스트."""

import io
from unittest.mock import patch

import pytest
from backend.api.routes import quiz as quiz_route
from backend.api.state import app_state
from backend.api.task_manager import task_manager
from fastapi import HTTPException, UploadFile

from src.config import Config
from src.quiz.models import QTYPE_MULTIPLE_CHOICE, QTYPE_SHORT_ANSWER, QuizQuestion
from src.scraper.models import Course, CourseDetail, LectureItem, LectureType, Week


class _FakeScraper:
    _page = object()


def _reset() -> None:
    app_state.scraper = None
    app_state.user_id = ""
    app_state.courses = []
    app_state.details = []
    task_manager.clear()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    import src.db as db_module

    monkeypatch.setattr(db_module, "_schema_ready_paths", set())
    monkeypatch.setattr(db_module, "_db_path", lambda: tmp_path / "app.db")
    monkeypatch.setattr(Config, "get_download_dir", staticmethod(lambda: str(tmp_path)))
    _reset()
    Config.AI_ENABLED = "true"
    Config.AI_AGENT = "gemini"
    Config.GOOGLE_API_KEY = "key"
    Config.GEMINI_MODEL = "gemini-x"
    yield
    _reset()


def _seed():
    course = Course(id="42", long_name="테스트 과목", href="/courses/42", term="2026-1")
    lecture = LectureItem(title="1강", item_url="/x", lecture_type=LectureType.MOVIE, week_label="1주차(총 15주)")
    detail = CourseDetail(
        course=course,
        course_name=course.long_name,
        professors="교수",
        weeks=[Week(title="1주차", week_number=1, lectures=[lecture])],
    )
    app_state.scraper = _FakeScraper()
    app_state.user_id = "student"
    app_state.courses = [course]
    app_state.details = [detail]
    return course


def _docx_bytes(text: str) -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_upload_material_and_list():
    _seed()
    data = _docx_bytes("업로드된 강의자료 본문 내용")
    upload = UploadFile(filename="자료.docx", file=io.BytesIO(data))

    meta = await quiz_route.upload_material(course_id="42", week_label="1주차", file=upload)
    assert meta["name"] == "자료.docx"
    assert meta["text_chars"] > 0
    assert meta["text_empty"] is False

    listed = await quiz_route.list_materials(course_id="42")
    assert len(listed["materials"]) == 1
    assert listed["materials"][0]["id"] == meta["id"]

    await quiz_route.delete_material(meta["id"])
    assert (await quiz_route.list_materials(course_id="42"))["materials"] == []


@pytest.mark.asyncio
async def test_upload_material_rejects_bad_type():
    _seed()
    upload = UploadFile(filename="a.txt", file=io.BytesIO(b"hi"))
    with pytest.raises(HTTPException) as exc:
        await quiz_route.upload_material(course_id="42", week_label="", file=upload)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_generate_quiz_set_flow():
    _seed()
    data = _docx_bytes("전자기학의 기본 법칙과 맥스웰 방정식에 대한 강의 자료")
    upload = UploadFile(filename="자료.docx", file=io.BytesIO(data))
    meta = await quiz_route.upload_material(course_id="42", week_label="1주차", file=upload)

    fake_questions = [
        QuizQuestion(
            qtype=QTYPE_MULTIPLE_CHOICE,
            question="Q1",
            choices=["a", "b", "c", "d", "e"],
            answer="1",
            explanation="해설",
        ),
        QuizQuestion(qtype=QTYPE_SHORT_ANSWER, question="Q2", answer="맥스웰", rubric="맥스웰"),
    ]
    with patch.object(quiz_route, "generate_quiz", return_value=fake_questions):
        resp = await quiz_route.generate_quiz_set(
            quiz_route.GenerateRequest(
                course_id="42",
                week_numbers=[1],
                material_ids=[meta["id"]],
                counts={QTYPE_MULTIPLE_CHOICE: 1, QTYPE_SHORT_ANSWER: 1},
            )
        )
        managed = task_manager.get(resp["task_id"])
        await managed.task

    assert managed.status == "completed"
    set_id = managed.result["set_id"]

    detail = await quiz_route.get_quiz_set(set_id)
    assert detail["question_count"] == 2
    # 풀이용 payload에는 정답이 없어야 한다.
    assert "answer" not in detail["questions"][0]
    assert detail["questions"][0]["choices"] == ["a", "b", "c", "d", "e"]

    listed = await quiz_route.list_quiz_sets(course_id="42")
    assert len(listed["quiz_sets"]) == 1


@pytest.mark.asyncio
async def test_submit_attempt_mc_only_grades_immediately():
    _seed()
    questions = [
        QuizQuestion(
            qtype=QTYPE_MULTIPLE_CHOICE, question="Q1", choices=["a", "b"], answer="1", explanation="b가 정답"
        ),
    ]
    from src.quiz import store

    quiz_set = store.create_quiz_set(
        course_id="42",
        course_name="테스트 과목",
        term="2026-1",
        weeks=["1주차"],
        counts={QTYPE_MULTIPLE_CHOICE: 1},
        source={},
        agent="gemini",
        model="m",
        questions=questions,
    )
    resp = await quiz_route.submit_attempt(
        quiz_set.id,
        quiz_route.SubmitAttemptRequest(answers=[quiz_route.AttemptAnswer(question_id=questions[0].id, answer="1")]),
    )
    assert resp["graded_status"] == "completed"
    assert resp["task_id"] is None

    result = await quiz_route.get_attempt(quiz_set.id, resp["attempt_id"])
    assert result["score_total"] == 100.0
    assert result["results"][0]["correct"] is True
    assert result["results"][0]["correct_answer"] == "1"


@pytest.mark.asyncio
async def test_submit_attempt_subjective_runs_ai_task():
    _seed()
    questions = [
        QuizQuestion(qtype=QTYPE_MULTIPLE_CHOICE, question="Q1", choices=["a", "b"], answer="0"),
        QuizQuestion(qtype=QTYPE_SHORT_ANSWER, question="Q2", answer="서울", rubric="서울"),
    ]
    from src.quiz import store
    from src.quiz.models import GradeResult

    quiz_set = store.create_quiz_set(
        course_id="42",
        course_name="테스트 과목",
        term="2026-1",
        weeks=["1주차"],
        counts={},
        source={},
        agent="gemini",
        model="m",
        questions=questions,
    )

    with patch.object(
        quiz_route,
        "grade_subjective",
        return_value=[GradeResult(question_id=questions[1].id, correct=True, score=80.0, feedback="좋음")],
    ):
        resp = await quiz_route.submit_attempt(
            quiz_set.id,
            quiz_route.SubmitAttemptRequest(
                answers=[
                    quiz_route.AttemptAnswer(question_id=questions[0].id, answer="0"),
                    quiz_route.AttemptAnswer(question_id=questions[1].id, answer="서울"),
                ]
            ),
        )
        assert resp["graded_status"] == "partial"
        managed = task_manager.get(resp["task_id"])
        await managed.task

    result = await quiz_route.get_attempt(quiz_set.id, resp["attempt_id"])
    assert result["graded_status"] == "completed"
    assert result["score_total"] == 90.0  # (100 + 80) / 2


@pytest.mark.asyncio
async def test_generate_requires_ai_enabled():
    _seed()
    Config.AI_ENABLED = "false"
    with pytest.raises(HTTPException) as exc:
        await quiz_route.generate_quiz_set(
            quiz_route.GenerateRequest(course_id="42", week_numbers=[1], counts={QTYPE_MULTIPLE_CHOICE: 1})
        )
    assert exc.value.status_code == 409
