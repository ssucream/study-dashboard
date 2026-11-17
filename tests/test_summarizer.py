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


def test_generate_text_ssu_uses_gateway_base_url():
    """숭실대 API Gateway는 openai SDK를 gateway base_url로 호출해야 한다."""
    from unittest.mock import MagicMock, patch

    from src.summarizer.summarizer import SSU_BASE_URL, generate_text

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="ssu 요약"))]
    )
    with patch("openai.OpenAI", return_value=fake_client) as mock_openai:
        assert generate_text("ssu", "key", "gpt-5-mini", "prompt") == "ssu 요약"
    assert mock_openai.call_args.kwargs["base_url"] == SSU_BASE_URL == "https://factchat-cloud.mindlogic.ai/v1/gateway"


def test_ssu_catalog_live_and_fallback():
    """키가 있으면 /models/?type=llm 결과를 등급순으로, 실패하거나 키가 없으면 추천 목록을 반환한다."""
    from unittest.mock import MagicMock, patch

    import requests

    from src.summarizer.summarizer import get_model_catalog

    resp = MagicMock()
    resp.json.return_value = {
        "data": [
            {"id": "claude-opus-5", "type": "llm"},
            {"id": "sonar-pro", "type": "llm"},
            {"id": "gpt-image-2", "type": "image"},
            {"id": "gemini-3.5-flash-lite", "type": "llm"},
            {"id": "gemini-3.5-flash-lite", "type": "llm"},
            {"id": "gpt-5.4-mini", "type": "llm"},
            {"id": "glm-5.3", "type": "llm"},
            {"id": ""},
        ]
    }
    with patch("src.summarizer.summarizer.requests.get", return_value=resp) as mock_get:
        models, source = get_model_catalog("ssu", "secret")
    assert source == "ssu-user"
    assert [(m["id"], m["tier"]) for m in models] == [
        ("gemini-3.5-flash-lite", "recommended"),
        ("glm-5.3", "standard"),
        ("claude-opus-5", "expensive"),
        ("gpt-5.4-mini", "legacy"),
    ]
    assert models[0]["name"].startswith("⭐ 추천")
    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer secret"}
    assert mock_get.call_args.kwargs["params"] == {"type": "llm"}

    with patch("src.summarizer.summarizer.requests.get", side_effect=requests.ConnectionError):
        models, source = get_model_catalog("ssu", "secret")
    assert source == "fallback" and all(m["tier"] == "recommended" for m in models)

    models, source = get_model_catalog("ssu", "")
    assert source == "fallback"


@pytest.mark.parametrize(
    ("model_id", "tier"),
    [
        ("gemini-3.5-flash-lite", "recommended"),
        ("gpt-6-luna", "recommended"),
        ("claude-opus-5-5", "expensive"),
        ("claude-fable-5-2", "expensive"),
        ("gpt-6.1-sol", "expensive"),
        ("deepseek-v4-pro", "expensive"),
        ("qwen3.8-max", "expensive"),
        ("gpt-5.4-pro", "legacy"),
        ("gpt-5.6-luna", "legacy"),
        ("sonar-reasoning-pro", "unsuitable"),
        ("gpt-audio-1.5", "unsuitable"),
        ("gpt-5.2-codex", "unsuitable"),
        ("claude-sonnet-5", "standard"),
        ("solar-mini4", "recommended"),
        ("solar-pro4", "expensive"),
    ],
)
def test_ssu_model_tier(model_id, tier):
    from src.summarizer.summarizer import ssu_model_tier

    assert ssu_model_tier(model_id)[0] == tier


# 숭실대 게이트웨이 모델 목록(2026-11 사용자 제공) — 텍스트 모델은 요약 불가로 막히면 안 되고, 그 외는 전부 막혀야 한다.
_SSU_LLM_IDS = [
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-6-astra",
    "gpt-5.6-luna",
    "gpt-5.4-pro",
    "gpt-5.6-terra",
    "gpt-5.2-pro",
    "gpt-5.6-sol",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.2",
    "gpt-5.1",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-opus-5-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-fable-5-1",
    "claude-opus-4-6",
    "claude-opus-4-7",
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-haiku-4-5-20251001",
    "claude-fable-5-2",
    "gemini-4-carbon",
    "gemini-4-hydrogen",
    "gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "grok-4.7",
    "grok-4.6",
    "grok-4.5",
    "grok-4-1-fast",
    "google/gemma-4-31B-it",
    "google/gemma-3-27b-it",
    "muse-spark-1.3",
    "solar-pro4",
    "solar-pro3",
    "solar-mini4",
    "qwen3.8-flash",
    "qwen3.8-max",
    "qwen3.7-plus",
    "qwen3.7-max",
    "glm-5.3-flash",
    "glm-5.3",
    "glm-5.2",
    "kimi-k3.1-pro",
    "kimi-k3",
    "seed-2-0-pro-260328",
    "seed-2-0-lite-260428",
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
]
_SSU_NON_TEXT_IDS = [
    "gpt-audio-1.5",
    "sonar-pro",
    "sonar-reasoning-pro",
    "gpt-image-2.6-sunburst",
    "gemini-nano-banana-2.1",
    "gemini-3.1-flash-image-preview",
    "gemini-3.1-flash-lite-image",
    "gemini-3-pro-image-preview",
    "gemini-2.5-flash-image",
    "gpt-image-2.6-flare",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
    "gpt-image-2",
    "fal-ai/flux-2-pro",
    "fal-ai/flux-pro/v1.1",
    "xai/grok-imagine-image/v2.0/text-to-image",
    "bytedance/seedream-5-pro",
    "fal-ai/bytedance/seedream/v5/lite",
    "fal-ai/bytedance/seedream/v4.5",
    "bytedance/seedream-4",
    "bytedance/seedream/v5/pro/layerize",
    "fal-ai/fast-sdxl",
    "ideogram/v4/fast",
    "fal-ai/ideogram/v3",
    "fal-ai/recraft/v4/text-to-image",
    "qwen-image-3.0-pro",
    "qwen-image-3.0",
    "z-image-turbo",
    "minimax/h3-max-turbo",
    "google/gemini-omni-flash/v1.2",
    "google/gemini-omni-flash/v1.1",
    "kling/v4",
    "fal-ai/kling-video/v3",
    "fal-ai/kling-video/o3",
    "fal-ai/kling-video/v2.5-turbo/pro",
    "xai/grok-imagine-video",
    "bytedance/seedance-2.5",
    "bytedance/seedance-2.0",
    "bytedance/seedance-2.0/fast",
    "bytedance/seedance-1-pro",
    "bytedance/seedance-1.0-pro/fast",
    "fal-ai/pixverse/v5",
    "fal-ai/minimax/hailuo-02",
    "minimax/h3-max",
    "fal-ai/luma-dream-machine/ray-2",
    "fal-ai/vidu/q3",
    "lightricks/ltx-2.5/fast",
    "lightricks/ltx-2.5/pro",
    "wan3.0-video-prime",
    "happyhorse-1.1-t2v",
    "wan3.0-video",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-3.8-flash-tts",
    "lyria-3-pro-preview",
    "lyria-3-clip-preview",
    "elevenlabs-music",
    "elevenlabs-sfx",
    "stt-async-v6",
    "stt-async-v5",
    "text-embedding-3-small",
    "text-embedding-3-large",
    "gemini-embedding-2",
    "text-embedding-v4",
    "qwen3.7-text-embedding",
    "tongyi-embedding-vision-plus",
    "qwen3-rerank",
    "jev-1.14.0",
    "jev-1.13.0",
    "gpt-6-luna-decisions",
    "gpt-realtime-2.1",
    "gpt-realtime-2.1-mini",
    "gemini-3.8-live",
    "gemini-3.8-live-extended-thinking",
]


def test_ssu_tier_covers_gateway_catalog():
    from src.summarizer.summarizer import _SSU_LEGACY, SSU_RECOMMENDED, ssu_model_tier

    assert [m for m in _SSU_LLM_IDS if ssu_model_tier(m)[0] == "unsuitable"] == []
    assert [m for m in _SSU_NON_TEXT_IDS if ssu_model_tier(m)[0] != "unsuitable"] == []
    # 추천·레거시 표의 ID와 레거시 대체 모델이 실제 게이트웨이 목록에 존재해야 한다
    assert set(_SSU_LLM_IDS) >= SSU_RECOMMENDED
    assert set(_SSU_LEGACY) <= set(_SSU_LLM_IDS)
    assert {r for r in _SSU_LEGACY.values() if r} <= set(_SSU_LLM_IDS)
