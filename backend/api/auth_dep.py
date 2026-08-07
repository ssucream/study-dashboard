"""라우트 전반에서 재사용하는 로그인 여부 체크.

각 route 파일에 복붙되어 있던 `_require_auth()`를 한 곳으로 모은 것.
"""

from backend.api.state import app_state
from fastapi import HTTPException


def require_auth() -> None:
    if not app_state.scraper:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
