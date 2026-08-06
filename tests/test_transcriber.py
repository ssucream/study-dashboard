"""transcriber.py 단위 테스트 — 경로 생성 로직만 검증 (Whisper 모델 로드 없음)."""

import sys
import threading
import time
from unittest.mock import MagicMock


def _setup_mock_faster_whisper():
    """faster_whisper 모듈을 mock으로 등록한다."""
    mock_module = MagicMock()
    sys.modules["faster_whisper"] = mock_module
    return mock_module


def test_transcribe_output_path(tmp_path):
    """출력 파일이 .txt 확장자로 생성되어야 한다."""
    _setup_mock_faster_whisper()
    audio = tmp_path / "lecture.mp3"
    audio.write_bytes(b"fake audio")

    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "테스트 텍스트"
    mock_model.transcribe.return_value = ([mock_segment], None)

    import src.stt.transcriber as mod

    mod._model_cache.clear()
    mod._model_cache["base"] = mock_model

    result = mod.transcribe(audio, model_size="base")
    assert result.suffix == ".txt"
    assert result.stem == "lecture"
    assert result.read_text(encoding="utf-8") == "테스트 텍스트"

    mod._model_cache.clear()


def test_transcribe_with_language(tmp_path):
    """language 파라미터가 전달되어야 한다."""
    _setup_mock_faster_whisper()
    audio = tmp_path / "lecture.mp4"
    audio.write_bytes(b"fake")

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], MagicMock())

    import src.stt.transcriber as mod

    mod._model_cache.clear()
    mod._model_cache["base"] = mock_model

    mod.transcribe(audio, model_size="base", language="ko")
    mock_model.transcribe.assert_called_once_with(str(audio), language="ko")

    mod._model_cache.clear()


def test_transcribe_without_language(tmp_path):
    """language가 빈 문자열이면 kwargs에 포함되지 않아야 한다."""
    _setup_mock_faster_whisper()
    audio = tmp_path / "lecture.mp4"
    audio.write_bytes(b"fake")

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], MagicMock())

    import src.stt.transcriber as mod

    mod._model_cache.clear()
    mod._model_cache["base"] = mock_model

    mod.transcribe(audio, model_size="base", language="")
    mock_model.transcribe.assert_called_once_with(str(audio))

    mod._model_cache.clear()


def test_transcribe_concurrent_different_model_sizes_no_race(tmp_path):
    """회귀 테스트: 서로 다른 model_size를 동시에 요청해도 _model_cache 경쟁으로 KeyError가 나면 안 된다."""
    mock_module = _setup_mock_faster_whisper()

    def make_model(size, device, compute_type):
        time.sleep(0.05)  # 캐시 clear()/대입 사이 경쟁 창을 넓힘
        model = MagicMock()
        segment = MagicMock()
        segment.text = f"{size} 텍스트"
        model.transcribe.return_value = ([segment], None)
        return model

    mock_module.WhisperModel.side_effect = make_model

    import src.stt.transcriber as mod

    mod._model_cache.clear()
    errors: list[Exception] = []

    def worker(size: str) -> None:
        try:
            audio = tmp_path / f"lecture_{size}.mp3"
            audio.write_bytes(b"fake")
            mod.transcribe(audio, model_size=size)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(size,)) for size in ("tiny", "base", "small")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    mod._model_cache.clear()
