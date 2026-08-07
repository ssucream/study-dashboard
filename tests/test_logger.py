"""logger.py FileHandler 누수 방지 테스트."""

from src.logger import close_error_logger, get_error_logger


def test_close_error_logger_releases_file_handler(tmp_path, monkeypatch):
    from src import logger as logger_module

    monkeypatch.setattr(logger_module, "_LOGS_DIR", tmp_path)

    logger, log_path = get_error_logger("test")
    logger.info("hello")
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]

    close_error_logger(logger)

    assert logger.handlers == []
    assert handler.stream is None  # FileHandler.close()가 내부 스트림을 닫음
    assert log_path.read_text(encoding="utf-8").strip().endswith("hello")
