"""API 요청 검증 유틸리티."""

from urllib.parse import urlparse

_ALLOWED_LECTURE_HOSTS = {"canvas.ssu.ac.kr"}


def validate_lecture_url(url: str) -> str:
    """lecture_url이 허용된 LMS 호스트를 가리키는지 검증한다 (SSRF 방지)."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_LECTURE_HOSTS:
        raise ValueError("허용되지 않은 강의 URL입니다.")
    return url
