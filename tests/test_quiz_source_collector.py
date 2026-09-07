"""src/quiz/source_collector.py — 컨텍스트 조립 테스트."""

from src.quiz.source_collector import LectureText, MaterialText, build_context


def test_build_context_labels_weeks_and_sources():
    lecture_texts = [
        LectureText(week_label="1주차", title="1강", summary_text="요약 A", stt_text="전사 A"),
        LectureText(week_label="2주차", title="2강", summary_text="요약 B", stt_text=""),
    ]
    result = build_context(
        week_labels=["1주차", "2주차"],
        lecture_texts=lecture_texts,
        materials=[MaterialText(name="자료.pdf", text="PDF 내용")],
        include_stt=True,
    )
    ctx = result.context
    assert "[주차] 1주차" in ctx
    assert "[요약본] 1강" in ctx
    assert "[강의 전사(STT)] 1강" in ctx
    assert "[업로드 자료] 자료.pdf" in ctx
    assert result.stats["summary_lectures"] == 2
    assert result.stats["stt_lectures"] == 1
    assert result.stats["materials"] == 1


def test_build_context_excludes_stt_when_disabled():
    lecture_texts = [LectureText(week_label="1주차", title="1강", summary_text="요약", stt_text="전사")]
    result = build_context(week_labels=["1주차"], lecture_texts=lecture_texts, include_stt=False)
    assert "전사" not in result.context
    assert result.stats["stt_lectures"] == 0


def test_build_context_truncates_over_budget():
    big = "가" * 5000
    lecture_texts = [LectureText(week_label="1주차", title="1강", summary_text=big)]
    result = build_context(week_labels=["1주차"], lecture_texts=lecture_texts, include_stt=False, max_chars=500)
    assert result.stats["truncated"] is True
    assert len(result.context) < 1000


def test_build_context_empty_when_no_text():
    result = build_context(
        week_labels=["1주차"],
        lecture_texts=[LectureText(week_label="1주차", title="1강")],
        include_stt=True,
    )
    assert result.context == ""
