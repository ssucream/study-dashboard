"""
민감 정보 암호화/복호화 유틸리티.

최초 실행 시 머신 고유 Fernet 키를 생성해서 .secret_key 파일에 저장한다.
같은 기기에서만 복호화 가능하므로 SQLite 설정 DB가 유출돼도 값을 읽을 수 없다.

암호화된 값은 "enc:" 접두사로 구별한다.
"""

import logging
import os
import time
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_PREFIX = "enc:"
_KEY_PATH = Path(__file__).parent.parent / ".secret_key"
_dir_warning_emitted = False


def _resolve_key_path() -> Path:
    """실제 키 파일 경로를 반환한다.

    Docker 바인드 마운트 시 호스트에 파일이 없으면 .secret_key가 디렉토리로
    생성되므로, 그 경우 디렉토리 내부의 key 파일을 사용한다.
    """
    global _dir_warning_emitted
    if _KEY_PATH.is_dir():
        if not _dir_warning_emitted:
            logger.warning(
                ".secret_key가 파일이 아닌 디렉토리입니다 (Docker 볼륨 마운트로 추정). "
                "키를 %s/key 에 저장합니다. 호스트에서 해당 경로를 유지해야 복호화가 가능합니다.",
                _KEY_PATH,
            )
            _dir_warning_emitted = True
        return _KEY_PATH / "key"
    return _KEY_PATH


def _load_or_create_key() -> bytes:
    """
    .secret_key 파일에서 키를 읽거나, 없으면 새로 생성해서 저장한다.
    .secret_key는 .gitignore에 등록되어야 한다.

    Docker 볼륨 마운트 시 .secret_key가 디렉토리로 생성될 수 있으므로
    디렉토리인 경우 내부의 key 파일을 사용한다.
    """
    key_file = _resolve_key_path()

    if key_file.exists() and key_file.is_file():
        return key_file.read_bytes().strip()

    key = Fernet.generate_key()
    try:
        # O_CREAT|O_EXCL로 원자적 생성 — 생성과 동시에 권한을 0o600으로 지정해
        # umask로 인한 일시적 세계-읽기 가능 창을 없애고, 동시 생성 경쟁을 방지한다.
        fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
    except FileExistsError:
        # 동시에 다른 프로세스/스레드가 먼저 키를 생성함 — 그 키를 사용해야 기존 암호화 값과 정합성이 맞음.
        # os.open(O_CREAT)와 os.write는 원자적이지 않아서, 파일이 생성된 직후·쓰기가 끝나기 전에
        # 읽으면 빈 바이트가 보일 수 있다. 쓰기가 끝날 때까지 짧게 재시도한다.
        for _ in range(50):
            data = key_file.read_bytes().strip()
            if data:
                return data
            time.sleep(0.01)
        return key_file.read_bytes().strip()
    return key


@lru_cache(maxsize=8)
def _fernet_for(key_file_str: str) -> Fernet:
    """키 파일 경로별로 Fernet 인스턴스를 캐시한다.

    암호화/복호화가 호출될 때마다 키 파일을 디스크에서 다시 읽지 않도록 한다.
    경로를 캐시 키로 삼아 테스트에서 _KEY_PATH를 바꿔치기해도 격리가 유지된다.
    """
    return Fernet(_load_or_create_key())


def _fernet() -> Fernet:
    return _fernet_for(str(_resolve_key_path()))


def encrypt(plaintext: str) -> str:
    """평문을 암호화하고 'enc:<base64>' 형태의 문자열을 반환한다."""
    token = _fernet().encrypt(plaintext.encode())
    return _PREFIX + token.decode()


def decrypt(value: str) -> str:
    """
    'enc:<base64>' 형태의 값을 복호화한다.
    접두사가 없으면 평문 그대로 반환한다 (하위 호환).
    복호화 실패 시 빈 문자열 반환.
    """
    if not value.startswith(_PREFIX):
        return value
    token = value[len(_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.warning("복호화 실패 — 암호화 키 불일치 또는 손상된 값입니다. 빈 문자열을 반환합니다.")
        return ""
    except Exception as e:
        logger.warning("복호화 중 예상 밖 오류: %s", e)
        return ""


def is_encrypted(value: str) -> bool:
    return value.startswith(_PREFIX)
