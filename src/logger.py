"""
로그 모듈.

오류 발생 시에만 logs/ 디렉토리에 로그 파일을 생성한다.
정상 동작 시에는 파일이 생성되지 않는다.

로그 파일 형식: logs/YYYYMMDD_HHMMSS_<action>.log
"""

import logging
from datetime import datetime
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent / "logs"


def get_error_logger(action: str) -> tuple[logging.Logger, Path]:
    """
    오류 기록용 파일 로거를 생성한다.

    Args:
        action: 로그 파일 이름에 포함할 동작 식별자 (예: "play", "download")

    Returns:
        (logger, log_path) — 로거와 로그 파일 경로
    """
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _LOGS_DIR / f"{timestamp}_{action}.log"

    logger = logging.getLogger(f"study_helper.{action}.{timestamp}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger, log_path


def close_error_logger(logger: logging.Logger) -> None:
    """get_error_logger()로 만든 로거의 FileHandler를 닫고 해제한다.

    호출하지 않으면 재생/다운로드 실패마다 fd가 누적되어 장시간 구동 시
    "Too many open files"로 이어진다.
    """
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
