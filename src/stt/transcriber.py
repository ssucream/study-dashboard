"""
Whisper STT 변환기.

mp3/mp4 파일을 faster-whisper에 직접 전달해 텍스트로 변환한다.
wav 중간 파일은 생성하지 않는다.
"""

import threading
from pathlib import Path

# 모델 싱글톤 캐시: 동일 크기 모델은 재사용, 다른 크기 요청 시 기존 해제
_model_cache: dict = {}
_model_cache_lock = threading.Lock()


def transcribe(
    audio_path: Path,
    model_size: str = "base",
    language: str = "",
    on_model_loaded: "callable | None" = None,
    output_path: Path | None = None,
) -> Path:
    """
    faster-whisper로 음성 파일을 텍스트로 변환한다.

    Args:
        audio_path: mp3 또는 mp4 파일 경로
        model_size: Whisper 모델 크기 (tiny/base/small/medium/large)
        language: 언어 코드 (예: "ko"). 빈 문자열이면 자동 감지.
        on_model_loaded: 모델 로딩 완료 시 호출되는 콜백 (선택). 스레드 안전해야 함.

    Returns:
        생성된 .txt 파일 경로
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "faster-whisper 패키지가 설치되어 있지 않습니다.\n설치: pip install faster-whisper"
        ) from None

    with _model_cache_lock:
        if model_size not in _model_cache:
            _model_cache.clear()
            _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
        model = _model_cache[model_size]

    if on_model_loaded is not None:
        try:
            on_model_loaded()
        except Exception:
            pass  # 콜백 실패가 STT 변환을 막지 않도록

    transcribe_kwargs = {}
    if language:
        transcribe_kwargs["language"] = language
    segments, _ = model.transcribe(str(audio_path), **transcribe_kwargs)
    text = "".join(segment.text for segment in segments)

    txt_path = output_path if output_path else audio_path.with_suffix(".txt")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text, encoding="utf-8")
    return txt_path
