"""
AI 요약기.

STT로 생성된 .txt 파일을 Gemini / OpenAI / OpenRouter API로 요약한다.
결과는 동일 경로에 _summarized.txt로 저장된다.
"""

from pathlib import Path

import requests

DEFAULT_SUMMARY_PROMPT = """\
당신은 대학교 강의 내용을 정리하는 전문 학습 보조 AI입니다.
아래는 강의를 음성 인식(STT)으로 변환한 텍스트입니다. STT 특성상 오탈자나 문장이 부자연스러운 부분이 있을 수 있으니 문맥을 고려해 이해해 주세요.

다음 형식에 맞춰 한국어로 요약해 주세요.
결과물은 일반 텍스트 파일로 저장되므로 #, *, **, -, ``` 같은 마크다운 기호는 절대 사용하지 마세요.
섹션 제목은 대괄호로 표시하고, 항목은 숫자나 줄바꿈으로 구분하세요.

형식 예시:

[강의 핵심 주제]
이번 강의에서 다루는 핵심 주제를 1~2문장으로 서술.

[주요 내용 정리]
1. 첫 번째 핵심 내용
2. 두 번째 핵심 내용
   - 소주제가 있으면 들여쓰기로 구분
3. ...

[핵심 용어 / 개념 정의]
용어1: 정의 및 설명
용어2: 정의 및 설명
(해당 없으면 이 섹션 생략)

[학습 포인트 요약]
1. 시험이나 과제에서 중요할 것 같은 내용
2. ...
3. ...

강의 텍스트:
{text}
"""

_EXTRA_PROMPT_TEMPLATE = """

추가 지시사항:
{extra}
"""

# 비전채플 과목 전용 추가 섹션
_CHAPEL_EXTRA_PROMPT = """\
이 강의는 채플(기독교 예배·강연) 형식입니다. 아래 두 섹션을 추가로 작성하세요.

[강연자 소개]
강연자의 이름, 소속(교회·기관·학교 등), 직함 또는 하는 일, 운영 단체를 텍스트에서 언급된 내용을 바탕으로 정리하세요.
강연자 정보가 전혀 언급되지 않으면 이 섹션을 생략하세요.

[성경 말씀]
강연에서 직접 인용되거나 언급된 성경 구절을 목록으로 정리하세요.
형식 예시:
1. 마태복음 5장 3절 — "심령이 가난한 자는 복이 있나니..."
2. 요한복음 3장 16절
성경 말씀이 전혀 언급되지 않으면 이 섹션을 생략하세요.\
"""

_GEMINI_MODELS = [
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite  (고속·저비용, 권장)"),
    ("gemini-3.6-flash", "Gemini 3.6 Flash  (최신 안정)"),
    ("gemini-3.5-flash", "Gemini 3.5 Flash"),
    ("gemini-3.1-pro-preview", "Gemini 3.1 Pro  (Preview)"),
    ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("gemini-2.5-pro", "Gemini 2.5 Pro"),
    ("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite"),
]

# 외부에서 모델 목록 참조용
GEMINI_MODEL_IDS = [m[0] for m in _GEMINI_MODELS]
GEMINI_MODEL_LABELS = [m[1] for m in _GEMINI_MODELS]
GEMINI_DEFAULT_MODEL = GEMINI_MODEL_IDS[0]

_OPENAI_MODELS = [
    ("gpt-5.6-luna", "GPT-5.6 Luna  (고속·저비용, 권장)"),
    ("gpt-5.6-terra", "GPT-5.6 Terra  (균형)"),
    ("gpt-5.6-sol", "GPT-5.6 Sol  (최고 성능)"),
    ("gpt-5.6", "GPT-5.6  (Sol 최신 별칭)"),
    ("gpt-5.5", "GPT-5.5"),
    ("gpt-5.4-mini", "GPT-5.4 mini"),
    ("gpt-5.4-nano", "GPT-5.4 nano  (최저 비용)"),
    ("gpt-5.4", "GPT-5.4"),
    ("gpt-5-mini", "GPT-5 mini"),
    ("gpt-5-nano", "GPT-5 nano"),
    ("gpt-4.1-mini", "GPT-4.1 mini  (레거시)"),
]

OPENAI_MODEL_IDS = [m[0] for m in _OPENAI_MODELS]
OPENAI_MODEL_LABELS = [m[1] for m in _OPENAI_MODELS]
OPENAI_DEFAULT_MODEL = OPENAI_MODEL_IDS[0]

_OPENROUTER_MODELS = [
    ("openrouter/auto", "OpenRouter Auto Router  (권장)"),
    ("~openai/gpt-mini-latest", "OpenAI GPT Mini Latest"),
    ("~openai/gpt-latest", "OpenAI GPT Latest"),
    ("google/gemini-3.5-flash-lite", "Google Gemini 3.5 Flash-Lite"),
    ("anthropic/claude-fable-5", "Anthropic Claude Fable 5"),
    ("openai/gpt-5.6-luna", "OpenAI GPT-5.6 Luna"),
    ("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("x-ai/grok-4.6", "xAI Grok 4.6"),
    ("openrouter/free", "OpenRouter Free Models Router"),
]

OPENROUTER_MODEL_IDS = [m[0] for m in _OPENROUTER_MODELS]
OPENROUTER_MODEL_LABELS = [m[1] for m in _OPENROUTER_MODELS]
OPENROUTER_DEFAULT_MODEL = OPENROUTER_MODEL_IDS[0]

# 설정 화면에서 provider 드롭다운 구성에 사용 (agent id, 표시 라벨, 모델 id 목록, 모델 라벨 목록, 기본 모델)
AI_AGENTS = [
    ("gemini", "Google Gemini", GEMINI_MODEL_IDS, GEMINI_MODEL_LABELS, GEMINI_DEFAULT_MODEL),
    ("openai", "OpenAI", OPENAI_MODEL_IDS, OPENAI_MODEL_LABELS, OPENAI_DEFAULT_MODEL),
    ("openrouter", "OpenRouter", OPENROUTER_MODEL_IDS, OPENROUTER_MODEL_LABELS, OPENROUTER_DEFAULT_MODEL),
]

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def get_model_catalog(agent: str, api_key: str = "") -> tuple[list[dict[str, str]], str]:
    """AI 설정 UI에 보여줄 텍스트 생성 모델 목록을 반환한다."""
    if agent == "gemini":
        return [{"id": model_id, "name": label} for model_id, label in _GEMINI_MODELS], "curated"
    if agent == "openai":
        return [{"id": model_id, "name": label} for model_id, label in _OPENAI_MODELS], "curated"
    if agent != "openrouter":
        raise ValueError(f"지원하지 않는 AI 에이전트: {agent}")

    fallback = [{"id": model_id, "name": label} for model_id, label in _OPENROUTER_MODELS]
    url = f"{OPENROUTER_MODELS_URL}/user" if api_key else OPENROUTER_MODELS_URL
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = requests.get(url, params={"output_modalities": "text"}, headers=headers, timeout=10)
        response.raise_for_status()
        catalog = []
        seen = set()
        for item in response.json().get("data", []):
            model_id = str(item.get("id") or "").strip()
            if not model_id or model_id in seen:
                continue
            output_modalities = (item.get("architecture") or {}).get("output_modalities") or ["text"]
            if "text" not in output_modalities:
                continue
            seen.add(model_id)
            catalog.append({"id": model_id, "name": str(item.get("name") or model_id)})
        if catalog:
            return catalog, "openrouter-user" if api_key else "openrouter-public"
    except (requests.RequestException, TypeError, ValueError):
        pass
    return fallback, "fallback"


def summarize(
    txt_path: Path,
    agent: str,
    api_key: str,
    model: str,
    extra_prompt: str = "",
    course_name: str = "",
    prompt_template: str = "",
    output_path: Path | None = None,
) -> Path:
    """
    텍스트 파일을 AI로 요약한다.

    Args:
        txt_path:     STT 결과 .txt 파일 경로
        agent:        "gemini" / "openai" / "openrouter"
        api_key:      agent에 해당하는 API 키
        model:        사용할 모델 ID
        extra_prompt: 사용자 추가 지시사항 (기본 프롬프트 뒤에 추가)
        course_name:  과목명 (비전채플 감지에 사용)
        prompt_template: 사용자 편집 요약 프롬프트. `{text}` placeholder 사용 가능

    Returns:
        생성된 _summarized.txt 파일 경로
    """
    text = txt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("텍스트 파일이 비어 있습니다.")

    prompt = build_summary_prompt(
        text,
        extra_prompt=extra_prompt,
        course_name=course_name,
        prompt_template=prompt_template,
    )

    if agent == "gemini":
        summary = _summarize_gemini(api_key, model, prompt)
    elif agent == "openai":
        summary = _summarize_openai(api_key, model, prompt)
    elif agent == "openrouter":
        summary = _summarize_openrouter(api_key, model, prompt)
    else:
        raise ValueError(f"지원하지 않는 AI 에이전트: {agent}")

    out_path = output_path if output_path else txt_path.with_stem(txt_path.stem + "_summarized")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(summary, encoding="utf-8")
    return out_path


def build_summary_prompt(
    text: str,
    *,
    extra_prompt: str = "",
    course_name: str = "",
    prompt_template: str = "",
) -> str:
    """요약 요청 프롬프트를 구성한다."""
    template = prompt_template or DEFAULT_SUMMARY_PROMPT
    if "{text}" in template:
        prompt = template.replace("{text}", text)
    else:
        prompt = f"{template.rstrip()}\n\n강의 텍스트:\n{text}"
    if "비전채플" in course_name:
        prompt += _EXTRA_PROMPT_TEMPLATE.format(extra=_CHAPEL_EXTRA_PROMPT)
    if extra_prompt:
        prompt += _EXTRA_PROMPT_TEMPLATE.format(extra=extra_prompt)
    return prompt


def _summarize_gemini(api_key: str, model: str, prompt: str) -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("google-genai 패키지가 설치되어 있지 않습니다.\n설치: pip install google-genai") from None

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig()
    if not model.startswith("gemini-3"):
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)
    response = client.models.generate_content(model=model, contents=prompt, config=config)
    if not response.text:
        finish_reason = None
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason
        raise RuntimeError(
            f"Gemini 응답이 비어 있습니다 (finish_reason={finish_reason}). 안전 필터 차단이나 토큰 제한일 수 있습니다."
        )
    return response.text


def _summarize_openai(api_key: str, model: str, prompt: str) -> str:
    return _summarize_openai_compatible(api_key, model, prompt, base_url=None, provider_label="OpenAI")


def _summarize_openrouter(api_key: str, model: str, prompt: str) -> str:
    return _summarize_openai_compatible(
        api_key, model, prompt, base_url="https://openrouter.ai/api/v1", provider_label="OpenRouter"
    )


def _summarize_openai_compatible(
    api_key: str, model: str, prompt: str, *, base_url: str | None, provider_label: str
) -> str:
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai 패키지가 설치되어 있지 않습니다.\n설치: pip install openai") from None

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    choice = response.choices[0] if response.choices else None
    content = choice.message.content if choice and choice.message else None
    if not content:
        finish_reason = choice.finish_reason if choice else None
        raise RuntimeError(
            f"{provider_label} 응답이 비어 있습니다 (finish_reason={finish_reason}). 안전 필터 차단이나 토큰 제한일 수 있습니다."
        )
    return content
