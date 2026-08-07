"""src/downloader/video_downloader.py 단위 테스트."""

import asyncio
import time
from unittest.mock import patch

import pytest

from src.downloader.video_downloader import _stream_download, download_video_with_browser


class _FakeContext:
    async def cookies(self):
        return []


class _FakePage:
    context = _FakeContext()


@pytest.mark.asyncio
async def test_download_video_with_browser_does_not_block_event_loop(monkeypatch, tmp_path):
    """회귀 테스트: 다운로드(동기 requests 스트리밍) 중에도 이벤트 루프의 다른 코루틴이 계속 진행돼야 한다."""

    def fake_stream_download(url, save_path, on_progress, attempt, cookies=None, referer=None):
        time.sleep(0.2)  # 동기 블로킹 I/O 시뮬레이션
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"data")

    monkeypatch.setattr("src.downloader.video_downloader._stream_download", fake_stream_download)

    tick_count = 0

    async def ticker():
        nonlocal tick_count
        while True:
            await asyncio.sleep(0.02)
            tick_count += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        result = await download_video_with_browser(_FakePage(), "https://cdn.example/video.mp4", tmp_path / "v.mp4")
    finally:
        ticker_task.cancel()

    assert result.is_file()
    # 0.2초짜리 블로킹 구간 동안 20ms 간격 ticker가 여러 번 돌아야 한다.
    # 이벤트 루프가 블로킹됐다면 ticker는 다운로드가 끝난 뒤에야 몰아서 돌게 되어 값이 매우 작다.
    assert tick_count >= 5


class _FakeResponse:
    """iter_content 도중 예외가 나도 with 블록을 벗어나면 close()가 불려야 함을 검증하는 fake."""

    def __init__(self):
        self.status_code = 200
        self.headers = {"content-length": "10"}
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield b"12345"
        raise ConnectionError("연결이 끊겼습니다.")

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def test_stream_download_closes_response_when_iter_content_raises(tmp_path):
    """회귀 테스트: 스트리밍 중 예외가 나도 requests 응답을 닫아 소켓을 누수하지 않아야 한다."""
    fake_response = _FakeResponse()

    with patch("src.downloader.video_downloader.requests.get", return_value=fake_response):
        with pytest.raises(ConnectionError):
            _stream_download(
                "https://cdn.example/video.mp4",
                tmp_path / "v.mp4",
                on_progress=None,
                attempt=1,
            )

    assert fake_response.closed is True
