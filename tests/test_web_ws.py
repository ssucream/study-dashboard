"""backend/api/routes/ws.py 테스트."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from backend.api.routes.ws import ws_status
from backend.api.state import app_state


class _FakeScraper:
    pass


@pytest.fixture(autouse=True)
def reset_state():
    app_state.scraper = None
    yield
    app_state.scraper = None


@pytest.mark.asyncio
async def test_ws_status_rejects_unauthenticated():
    """회귀 테스트: 로그인하지 않은 상태에서는 /ws/status 연결을 수락하지 말고 거부해야 한다."""
    ws = AsyncMock()
    app_state.scraper = None

    await ws_status(ws)

    ws.accept.assert_not_called()
    ws.close.assert_awaited_once_with(code=1008)
    ws.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_ws_status_accepts_authenticated_and_streams():
    """인증된 상태에서는 연결을 수락하고 상태 payload를 push해야 한다."""
    app_state.scraper = _FakeScraper()
    ws = AsyncMock()

    async def fake_send_text(data):
        json.loads(data)  # 유효한 JSON이어야 함
        raise asyncio.CancelledError()  # 무한 루프 탈출용

    ws.send_text.side_effect = fake_send_text

    with pytest.raises(asyncio.CancelledError):
        await ws_status(ws)

    ws.accept.assert_awaited_once()
    ws.close.assert_not_called()
    ws.send_text.assert_awaited_once()
