"""
AI 요약기.

STT로 생성된 .txt 파일을 Gemini / OpenAI / OpenRouter API로 요약한다.
결과는 동일 경로에 _summarized.txt로 저장된다.
"""

from pathlib import Path

import requests

# 요약 1건에 허용하는 최대 대기 시간(초). 긴 전사본이라도 이 안에 끝나야 하며,
# 초과 시 executor 스레드를 무한정 붙잡지 않도록 명시적으로 끊는다.
_SUMMARY_REQUEST_TIMEOUT = 300

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

# 채플(비전채플 등) 과목 감지 키워드. 과목명에서 공백을 제거하고 소문자로 맞춰 비교하므로
# "비전 채플", "비전채플Ⅱ (12345)", "Chapel" 등 표기 변형도 매칭된다.
_CHAPEL_KEYWORDS = ("채플", "chapel")


def is_chapel_course(course_name: str) -> bool:
    """과목명이 채플 과목으로 보이면 True."""
    normalized = (course_name or "").replace(" ", "").lower()
    return any(keyword in normalized for keyword in _CHAPEL_KEYWORDS)


# 채플 과목 전용 추가 섹션
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

# 숭실대 API Gateway (mindlogic) — OpenAI 호환. 실제 목록은 키로 /models/를 조회하고, 실패 시 아래 목록을 쓴다.
SSU_BASE_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway"
_SSU_MODELS = [
    ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
    ("gpt-6-luna", "GPT-6 Luna"),
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ("gemini-3.8-flash", "Gemini 3.8 Flash"),
    ("solar-mini4", "Solar Mini 4  (한국어 특화)"),
]

# 요약·예상문제용 등급. 게이트웨이 /models/에는 가격 필드가 없어 모델 계열명으로 분류한다(2026-11 문서 기준).
# shortcut: 이름 규칙 기반 분류 — 게이트웨이가 가격/등급 필드를 제공하면 그 값으로 교체할 것.
SSU_RECOMMENDED = {model_id for model_id, _ in _SSU_MODELS}
_SSU_LEGACY = {
    "claude-opus-4-6": "claude-opus-4-8",
    "claude-opus-4-7": "claude-opus-5",
    "claude-sonnet-4-6": "claude-sonnet-5",
    "gpt-5.6-sol": "gpt-6.1-sol",
    "gpt-5.6-terra": "gpt-6.1-terra",
    "gpt-5.6-luna": "gpt-6-luna",
    "gpt-5.1": "gpt-6-luna",
    "gpt-5.2": "gpt-6.1-terra",
    "gpt-5.2-pro": "",
    "gpt-5.4": "gpt-6.1-terra",
    "gpt-5.4-mini": "gpt-6-luna",
    "gpt-5.4-nano": "gpt-6-luna",
    "gpt-5.4-pro": "",
    "gemini-3.1-flash-lite": "gemini-3.5-flash-lite",
    "solar-pro3": "solar-pro4",
    "google/gemma-3-27b-it": "google/gemma-4-31B-it",
}
_SSU_EXPENSIVE_MARKERS = ("opus", "fable", "-pro", "-sol", "-max", "astra")
# 텍스트 요약에 쓸 수 없는 모델: 웹검색 전용·오디오·codex(Responses 전용)·이미지·영상·임베딩·리랭크·실시간 등
# 목록 드롭다운은 type=llm으로 걸러지므로, 이 표지는 모델 ID를 직접 입력한 경우의 저장 거부용이다.
_SSU_UNSUITABLE_MARKERS = (
    "sonar",
    "gpt-audio",
    "codex",
    "-tts",
    "stt-",
    "lyria",
    "elevenlabs",
    "image",
    "imagine",
    "nano-banana",
    "video",
    "seedance",
    "kling",
    "omni",
    "wan3",
    "happyhorse",
    "ideogram",
    "fal-ai/",
    "bytedance/",
    "minimax/",
    "lightricks/",
    "embedding",
    "rerank",
    "decisions",
    "jev-",
    "realtime",
    "-live",
)
_SSU_TIER_ORDER = {"recommended": 0, "standard": 1, "expensive": 2, "legacy": 3}


def ssu_model_tier(model_id: str) -> tuple[str, str]:
    """숭실대 게이트웨이 모델의 (등급, 안내 문구). 등급: recommended/standard/expensive/legacy/unsuitable."""
    mid = model_id.lower()
    if any(m in mid for m in _SSU_UNSUITABLE_MARKERS):
        return "unsuitable", "텍스트 요약에 쓸 수 없는 모델 (웹검색·이미지·영상·오디오·임베딩 등 전용)"
    if model_id in _SSU_LEGACY:
        repl = _SSU_LEGACY[model_id]
        return "legacy", f"레거시 모델 — {repl} 사용 권장" if repl else "레거시 모델 — 곧 종료될 수 있음"
    if model_id in SSU_RECOMMENDED:
        return "recommended", "요약·예상문제에 적합하고 크레딧 소모가 적음"
    if any(m in mid for m in _SSU_EXPENSIVE_MARKERS):
        return "expensive", "상위 등급 모델 — 강의 요약에는 과하고 크레딧 소모가 큼"
    return "standard", ""


_SSU_TIER_PREFIX = {"recommended": "⭐ 추천 · ", "expensive": "💸 고비용 · ", "legacy": "⚠️ 레거시 · "}


def _ssu_catalog_entry(model_id: str, name: str) -> dict[str, str]:
    tier, note = ssu_model_tier(model_id)
    return {"id": model_id, "name": _SSU_TIER_PREFIX.get(tier, "") + name, "tier": tier, "note": note}


SSU_MODEL_IDS = [m[0] for m in _SSU_MODELS]
SSU_MODEL_LABELS = [m[1] for m in _SSU_MODELS]
SSU_DEFAULT_MODEL = SSU_MODEL_IDS[0]

# 설정 화면에서 provider 드롭다운 구성에 사용 (agent id, 표시 라벨, 모델 id 목록, 모델 라벨 목록, 기본 모델)
AI_AGENTS = [
    ("gemini", "Google Gemini", GEMINI_MODEL_IDS, GEMINI_MODEL_LABELS, GEMINI_DEFAULT_MODEL),
    ("openai", "OpenAI", OPENAI_MODEL_IDS, OPENAI_MODEL_LABELS, OPENAI_DEFAULT_MODEL),
    ("openrouter", "OpenRouter", OPENROUTER_MODEL_IDS, OPENROUTER_MODEL_LABELS, OPENROUTER_DEFAULT_MODEL),
    ("ssu", "숭실대 API Gateway", SSU_MODEL_IDS, SSU_MODEL_LABELS, SSU_DEFAULT_MODEL),
]

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def get_model_catalog(agent: str, api_key: str = "") -> tuple[list[dict[str, str]], str]:
    """AI 설정 UI에 보여줄 텍스트 생성 모델 목록을 반환한다."""
    if agent == "gemini":
        return [{"id": model_id, "name": label} for model_id, label in _GEMINI_MODELS], "curated"
    if agent == "openai":
        return [{"id": model_id, "name": label} for model_id, label in _OPENAI_MODELS], "curated"
    if agent == "ssu":
        return _get_ssu_model_catalog(api_key)
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


def _get_ssu_model_catalog(api_key: str) -> tuple[list[dict[str, str]], str]:
    fallback = [_ssu_catalog_entry(model_id, label) for model_id, label in _SSU_MODELS]
    if not api_key:
        return fallback, "fallback"
    try:
        response = requests.get(
            f"{SSU_BASE_URL}/models/",
            params={"type": "llm"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        # shortcut: 응답 스키마 문서 미확인 — OpenAI 형식({"data": [...]})과 배열 둘 다 허용
        items = payload.get("data", []) if isinstance(payload, dict) else payload
        catalog = []
        for item in items or []:
            model_id = str((item or {}).get("id") or "").strip() if isinstance(item, dict) else ""
            if not model_id or model_id in {m["id"] for m in catalog}:
                continue
            # 이미지·임베딩 등 비텍스트 모델과 요약 불가 모델은 목록에서 아예 뺀다
            if item.get("type", "llm") != "llm" or ssu_model_tier(model_id)[0] == "unsuitable":
                continue
            catalog.append(_ssu_catalog_entry(model_id, str(item.get("name") or model_id)))
        if catalog:
            catalog.sort(key=lambda m: _SSU_TIER_ORDER[m["tier"]])
            return catalog, "ssu-user"
    except (requests.RequestException, TypeError, ValueError, AttributeError):
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
    chapel_section: bool = True,
    output_path: Path | None = None,
) -> Path:
    """
    텍스트 파일을 AI로 요약한다.

    Args:
        txt_path:     STT 결과 .txt 파일 경로
        agent:        "gemini" / "openai" / "openrouter" / "ssu"
        api_key:      agent에 해당하는 API 키
        model:        사용할 모델 ID
        extra_prompt: 사용자 추가 지시사항 (기본 프롬프트 뒤에 추가)
        course_name:  과목명 (채플 과목 감지에 사용)
        prompt_template: 사용자 편집 요약 프롬프트. `{text}` placeholder 사용 가능
        chapel_section: 채플 과목이면 [강연자 소개]/[성경 말씀] 섹션 자동 추가 여부

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
        chapel_section=chapel_section,
    )

    summary = generate_text(agent, api_key, model, prompt)

    out_path = output_path if output_path else txt_path.with_stem(txt_path.stem + "_summarized")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(summary, encoding="utf-8")
    return out_path


def generate_text(agent: str, api_key: str, model: str, prompt: str) -> str:
    """provider(agent)에 맞는 텍스트 생성 API를 호출해 응답 문자열을 반환한다.

    요약(summarize) 외에 예상 문제 생성·채점(src/quiz)에서도 이 진입점을 재사용한다.
    """
    if agent == "gemini":
        return _summarize_gemini(api_key, model, prompt)
    if agent == "openai":
        return _summarize_openai(api_key, model, prompt)
    if agent == "openrouter":
        return _summarize_openrouter(api_key, model, prompt)
    if agent == "ssu":
        return _summarize_openai_compatible(
            api_key, model, prompt, base_url=SSU_BASE_URL, provider_label="숭실대 API Gateway"
        )
    raise ValueError(f"지원하지 않는 AI 에이전트: {agent}")


def build_summary_prompt(
    text: str,
    *,
    extra_prompt: str = "",
    course_name: str = "",
    prompt_template: str = "",
    chapel_section: bool = True,
) -> str:
    """요약 요청 프롬프트를 구성한다."""
    template = prompt_template or DEFAULT_SUMMARY_PROMPT
    if "{text}" in template:
        prompt = template.replace("{text}", text)
    else:
        prompt = f"{template.rstrip()}\n\n강의 텍스트:\n{text}"
    if chapel_section and is_chapel_course(course_name):
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

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=_SUMMARY_REQUEST_TIMEOUT * 1000),  # ms 단위
    )
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

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=_SUMMARY_REQUEST_TIMEOUT)
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
