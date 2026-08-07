"""
AI 요약기.

STT로 생성된 .txt 파일을 Gemini API로 요약한다.
결과는 동일 경로에 _summarized.txt로 저장된다.
"""

from pathlib import Path

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
    ("gemini-2.5-flash", "Gemini 2.5 Flash  (무료 티어 지원, 권장)"),
    ("gemini-2.0-flash", "Gemini 2.0 Flash  (무료 티어 지원)"),
    ("gemini-1.5-flash", "Gemini 1.5 Flash  (무료 티어 지원)"),
    ("gemini-1.5-pro", "Gemini 1.5 Pro    (유료)"),
]

# 외부에서 모델 목록 참조용
GEMINI_MODEL_IDS = [m[0] for m in _GEMINI_MODELS]
GEMINI_MODEL_LABELS = [m[1] for m in _GEMINI_MODELS]
GEMINI_DEFAULT_MODEL = GEMINI_MODEL_IDS[0]


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
        agent:        "gemini" (고정)
        api_key:      Gemini API 키
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
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    if not response.text:
        finish_reason = None
        if response.candidates:
            finish_reason = response.candidates[0].finish_reason
        raise RuntimeError(
            f"Gemini 응답이 비어 있습니다 (finish_reason={finish_reason}). 안전 필터 차단이나 토큰 제한일 수 있습니다."
        )
    return response.text
