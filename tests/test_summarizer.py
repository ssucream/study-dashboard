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
    """Gemini 목록은 2026-08 안정 모델을 기본으로 사용한다."""
    from src.summarizer.summarizer import GEMINI_DEFAULT_MODEL, GEMINI_MODEL_IDS

    assert len(GEMINI_MODEL_IDS) > 0
    assert GEMINI_DEFAULT_MODEL in GEMINI_MODEL_IDS
    assert GEMINI_DEFAULT_MODEL == "gemini-3.5-flash-lite"
    assert "gemini-3.6-flash" in GEMINI_MODEL_IDS
    assert "gemini-2.0-flash" not in GEMINI_MODEL_IDS


def test_build_summary_prompt_tolerates_stray_braces():
    """사용자 편집 템플릿에 {text} 외의 {..}가 섞여도 KeyError가 나면 안 된다."""
    from src.summarizer.summarizer import build_summary_prompt

    prompt = build_summary_prompt(
        "강의 내용",
        prompt_template="설정값 {GOOGLE_API_KEY} 참고해서 요약:\n{text}",
    )
    assert "강의 내용" in prompt
    assert "{GOOGLE_API_KEY}" in prompt


@pytest.mark.parametrize(
    "course_name, expected",
    [
        ("비전채플 (12345)", True),
        ("비전 채플", True),
        ("비전채플Ⅱ", True),
        ("채플", True),
        ("Global Chapel", True),
        ("자료구조", False),
        ("", False),
    ],
)
def test_is_chapel_course(course_name, expected):
    from src.summarizer.summarizer import is_chapel_course

    assert is_chapel_course(course_name) is expected


def test_build_summary_prompt_adds_chapel_sections_for_chapel_course():
    from src.summarizer.summarizer import build_summary_prompt

    prompt = build_summary_prompt("설교 내용", course_name="비전 채플 (99999)")
    assert "[강연자 소개]" in prompt
    assert "[성경 말씀]" in prompt


def test_build_summary_prompt_skips_chapel_sections_when_disabled():
    from src.summarizer.summarizer import build_summary_prompt

    prompt = build_summary_prompt("설교 내용", course_name="비전채플", chapel_section=False)
    assert "[강연자 소개]" not in prompt


def test_build_summary_prompt_no_chapel_sections_for_regular_course():
    from src.summarizer.summarizer import build_summary_prompt

    prompt = build_summary_prompt("강의 내용", course_name="운영체제")
    assert "[강연자 소개]" not in prompt


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


def test_openai_model_ids():
    """OpenAI 목록은 GPT-5.6 계열을 기본으로 사용한다."""
    from src.summarizer.summarizer import OPENAI_DEFAULT_MODEL, OPENAI_MODEL_IDS

    assert len(OPENAI_MODEL_IDS) > 0
    assert OPENAI_DEFAULT_MODEL in OPENAI_MODEL_IDS
    assert OPENAI_DEFAULT_MODEL == "gpt-5.6-luna"
    assert {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"} <= set(OPENAI_MODEL_IDS)


def test_openrouter_model_ids():
    """OpenRouter 모델 ID 목록이 비어있지 않아야 한다."""
    from src.summarizer.summarizer import OPENROUTER_DEFAULT_MODEL, OPENROUTER_MODEL_IDS

    assert len(OPENROUTER_MODEL_IDS) > 0
    assert OPENROUTER_DEFAULT_MODEL in OPENROUTER_MODEL_IDS


def test_openrouter_catalog_uses_account_filtered_endpoint():
    """저장된 키가 있으면 OpenRouter 계정 정책을 반영한 모델 목록을 조회한다."""
    from unittest.mock import MagicMock

    from src.summarizer.summarizer import get_model_catalog

    response = MagicMock()
    response.json.return_value = {
        "data": [
            {
                "id": "vendor/text-model",
                "name": "Vendor Text Model",
                "architecture": {"output_modalities": ["text"]},
            },
            {
                "id": "vendor/image-model",
                "name": "Vendor Image Model",
                "architecture": {"output_modalities": ["image"]},
            },
        ]
    }

    with patch("src.summarizer.summarizer.requests.get", return_value=response) as mock_get:
        models, source = get_model_catalog("openrouter", "secret")

    assert models == [{"id": "vendor/text-model", "name": "Vendor Text Model"}]
    assert source == "openrouter-user"
    assert mock_get.call_args.args[0].endswith("/models/user")
    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer secret"}


def test_openrouter_catalog_falls_back_when_api_fails():
    """OpenRouter 모델 API 장애 시에도 최신 기본 목록을 제공한다."""
    import requests

    from src.summarizer.summarizer import get_model_catalog

    with patch("src.summarizer.summarizer.requests.get", side_effect=requests.Timeout):
        models, source = get_model_catalog("openrouter")

    assert source == "fallback"
    assert any(model["id"] == "openrouter/auto" for model in models)


def test_summarize_openai_empty_response_raises():
    """OpenAI가 빈 응답을 주면 명확한 RuntimeError."""
    from unittest.mock import MagicMock

    from src.summarizer.summarizer import _summarize_openai

    fake_choice = MagicMock(message=MagicMock(content=None), finish_reason="content_filter")
    fake_response = MagicMock(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    with patch("openai.OpenAI", return_value=fake_client):
        with pytest.raises(RuntimeError, match="비어 있습니다"):
            _summarize_openai("key", "model", "prompt")


def test_summarize_openai_success():
    """OpenAI 응답이 정상이면 텍스트를 그대로 반환한다."""
    from unittest.mock import MagicMock

    from src.summarizer.summarizer import _summarize_openai

    fake_choice = MagicMock(message=MagicMock(content="요약 결과"))
    fake_response = MagicMock(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    with patch("openai.OpenAI", return_value=fake_client):
        assert _summarize_openai("key", "model", "prompt") == "요약 결과"


def test_summarize_openrouter_uses_openrouter_base_url():
    """OpenRouter는 openai SDK를 openrouter base_url로 호출해야 한다."""
    from unittest.mock import MagicMock, patch

    from src.summarizer.summarizer import _summarize_openrouter

    fake_choice = MagicMock(message=MagicMock(content="요약 결과"))
    fake_response = MagicMock(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    with patch("openai.OpenAI", return_value=fake_client) as mock_openai:
        result = _summarize_openrouter("key", "model", "prompt")

    assert result == "요약 결과"
    assert mock_openai.call_args.kwargs["base_url"] == "https://openrouter.ai/api/v1"


def test_summarize_routes_to_openai_and_openrouter(tmp_path):
    """summarize()가 agent에 따라 올바른 provider 함수를 호출한다."""
    txt = tmp_path / "lecture.txt"
    txt.write_text("강의 내용입니다.", encoding="utf-8")
    from src.summarizer.summarizer import summarize

    with patch("src.summarizer.summarizer._summarize_openai", return_value="openai 요약") as mock_openai:
        result = summarize(txt, agent="openai", api_key="key", model="model")
        mock_openai.assert_called_once()
        assert result.read_text(encoding="utf-8") == "openai 요약"

    with patch("src.summarizer.summarizer._summarize_openrouter", return_value="openrouter 요약") as mock_or:
        result = summarize(txt, agent="openrouter", api_key="key", model="model")
        mock_or.assert_called_once()
        assert result.read_text(encoding="utf-8") == "openrouter 요약"
