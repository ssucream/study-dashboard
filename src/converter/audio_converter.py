"""
mp4 → mp3 변환.

ffmpeg를 subprocess로 호출하여 영상에서 오디오를 추출한다.
"""

import subprocess
from pathlib import Path


def convert_to_mp3(mp4_path: Path, mp3_path: Path | None = None) -> Path:
    """
    mp4 파일을 mp3로 변환한다.

    Args:
        mp4_path: 원본 mp4 파일 경로
        mp3_path: 저장할 mp3 경로. None이면 mp4와 같은 위치에 확장자만 변경.

    Returns:
        저장된 mp3 파일의 Path

    Raises:
        FileNotFoundError: mp4 파일이 없거나 ffmpeg가 설치되지 않은 경우
        RuntimeError: ffmpeg 변환 실패 또는 타임아웃 시
    """
    if not mp4_path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {mp4_path}")

    if mp3_path is None:
        mp3_path = mp4_path.with_suffix(".mp3")

    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",  # 덮어쓰기 허용
        "-i",
        str(mp4_path),  # 입력 파일
        "-vn",  # 비디오 스트림 제외
        "-acodec",
        "libmp3lame",
        "-q:a",
        "2",  # VBR 품질 (0=최고, 9=최저), 2 ≈ 192kbps
        str(mp3_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except FileNotFoundError:
        raise FileNotFoundError("ffmpeg가 설치되어 있지 않습니다. ffmpeg를 먼저 설치해주세요.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("mp3 변환이 30분을 초과해 중단되었습니다. 원본 파일이 손상되었을 수 있습니다.") from None

    if result.returncode != 0:
        raise RuntimeError(f"mp3 변환 실패:\n{result.stderr[-500:]}")

    return mp3_path.resolve()
