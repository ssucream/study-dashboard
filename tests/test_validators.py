"""backend/api/validators.py 단위 테스트."""

import pytest
from backend.api.validators import validate_lecture_url


def test_validate_lecture_url_allows_canvas_https():
    url = "https://canvas.ssu.ac.kr/courses/1/items/1"
    assert validate_lecture_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://canvas.ssu.ac.kr/courses/1",  # http (non-https)
        "https://evil.example/courses/1",  # 허용되지 않은 호스트
        "http://169.254.169.254/latest/meta-data",  # SSRF 대상 (클라우드 메타데이터)
        "file:///etc/passwd",  # 파일 스킴
        "https://canvas.ssu.ac.kr.evil.example/x",  # 호스트 접미사 위장
    ],
)
def test_validate_lecture_url_rejects_disallowed(url):
    with pytest.raises(ValueError, match="허용되지 않은"):
        validate_lecture_url(url)
