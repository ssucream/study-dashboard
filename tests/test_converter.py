"""audio_converter.py 단위 테스트."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.converter.audio_converter import convert_to_mp3


def test_convert_to_mp3_default_path(tmp_path):
    """mp3_path 미지정 시 mp4와 같은 위치에 .mp3로 저장."""
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = convert_to_mp3(mp4)
        assert result.suffix == ".mp3"
        assert result.stem == "video"


def test_convert_to_mp3_custom_path(tmp_path):
    """mp3_path 지정 시 해당 경로에 저장."""
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake")
    custom = tmp_path / "output" / "custom.mp3"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = convert_to_mp3(mp4, mp3_path=custom)
        assert result.name == "custom.mp3"


def test_convert_to_mp3_missing_file():
    """존재하지 않는 파일은 FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        convert_to_mp3(Path("/nonexistent/file.mp4"))


def test_convert_to_mp3_ffmpeg_failure(tmp_path):
    """ffmpeg 실패 시 RuntimeError."""
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="encoding error")
        with pytest.raises(RuntimeError, match="mp3 변환 실패"):
            convert_to_mp3(mp4)


def test_convert_to_mp3_timeout(tmp_path):
    """ffmpeg가 무한 대기하면 timeout으로 RuntimeError."""
    import subprocess

    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake")
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1800)):
        with pytest.raises(RuntimeError, match="30분을 초과"):
            convert_to_mp3(mp4)
