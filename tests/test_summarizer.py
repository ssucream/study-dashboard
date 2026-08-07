"""summarizer.py 단위 테스트."""

from unittest.mock import patch

import pytest


def test_summarize_empty_text(tmp_path):
    """빈 텍스트 파일은 ValueError를 발생시켜야 한다."""
    txt = tmp_path / "empty.txt"
    txt.write_text("", encoding="utf-8")
    from src.summarizer.summarizer import summarize

    with pytest.raises(ValueError, match="비어 있습니다"):
        summarize(txt, agent="gemini", api_key="key", model="model")


def test_summarize_output_path(tmp_path):
    """출력 파일명이 _summarized.txt로 끝나야 한다."""
    txt = tmp_path / "lecture.txt"
    txt.write_text("강의 내용입니다.", encoding="utf-8")
    with patch("src.summarizer.summarizer._summarize_gemini", return_value="요약 결과"):
        from src.summarizer.summarizer import summarize

        result = summarize(txt, agent="gemini", api_key="key", model="model")
        assert result.name == "lecture_summarized.txt"
        assert result.read_text(encoding="utf-8") == "요약 결과"


def test_summarize_uses_custom_prompt_template(tmp_path):
    """사용자 편집 프롬프트 템플릿을 Gemini 호출에 반영한다."""
    txt = tmp_path / "lecture.txt"
    txt.write_text("강의 내용입니다.", encoding="utf-8")
    captured = {}

    def fake_summarize(api_key, model, prompt):
        captured["prompt"] = prompt
        return "요약 결과"

    with patch("src.summarizer.summarizer._summarize_gemini", side_effect=fake_summarize):
        from src.summarizer.summarizer import summarize

        summarize(
            txt,
            agent="gemini",
            api_key="key",
            model="model",
            prompt_template="커스텀 프롬프트\n{text}",
        )

    assert "커스텀 프롬프트" in captured["prompt"]
    assert "강의 내용입니다." in captured["prompt"]


def test_summarize_unsupported_agent(tmp_path):
    """지원하지 않는 에이전트는 ValueError."""
    txt = tmp_path / "test.txt"
    txt.write_text("내용", encoding="utf-8")
    from src.summarizer.summarizer import summarize

    with pytest.raises(ValueError, match="지원하지 않는"):
        summarize(txt, agent="claude", api_key="key", model="model")


def test_gemini_model_ids():
    """모델 ID 목록이 비어있지 않아야 한다."""
    from src.summarizer.summarizer import GEMINI_DEFAULT_MODEL, GEMINI_MODEL_IDS

    assert len(GEMINI_MODEL_IDS) > 0
    assert GEMINI_DEFAULT_MODEL in GEMINI_MODEL_IDS


def test_build_summary_prompt_tolerates_stray_braces():
    """사용자 편집 템플릿에 {text} 외의 {..}가 섞여도 KeyError가 나면 안 된다."""
    from src.summarizer.summarizer import build_summary_prompt

    prompt = build_summary_prompt(
        "강의 내용",
        prompt_template="설정값 {GOOGLE_API_KEY} 참고해서 요약:\n{text}",
    )
    assert "강의 내용" in prompt
    assert "{GOOGLE_API_KEY}" in prompt


def test_summarize_gemini_empty_response_raises():
    """Gemini가 안전 필터 등으로 빈 응답을 주면 명확한 RuntimeError."""
    from unittest.mock import MagicMock

    from src.summarizer.summarizer import _summarize_gemini

    fake_response = MagicMock(text=None, candidates=[MagicMock(finish_reason="SAFETY")])
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response

    with patch("google.genai.Client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="비어 있습니다"):
            _summarize_gemini("key", "model", "prompt")
