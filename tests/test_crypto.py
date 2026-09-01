"""crypto.py 단위 테스트."""

import stat
import threading
from unittest.mock import patch


def test_encrypt_decrypt_roundtrip(tmp_path):
    """암호화 후 복호화하면 원본과 동일해야 한다."""
    key_file = tmp_path / ".secret_key"
    with patch("src.crypto._KEY_PATH", key_file):
        from src.crypto import decrypt, encrypt, is_encrypted

        original = "test_password_123!@#"
        encrypted = encrypt(original)
        assert is_encrypted(encrypted)
        assert encrypted.startswith("enc:")
        assert decrypt(encrypted) == original


def test_decrypt_plaintext():
    """enc: 접두사 없는 평문은 그대로 반환해야 한다."""
    from src.crypto import decrypt

    assert decrypt("plain_value") == "plain_value"


def test_is_encrypted():
    """enc: 접두사 판별이 정확해야 한다."""
    from src.crypto import is_encrypted

    assert is_encrypted("enc:abc123") is True
    assert is_encrypted("plain") is False
    assert is_encrypted("") is False


def test_encrypt_empty_string(tmp_path):
    """빈 문자열도 암호화/복호화 가능해야 한다."""
    key_file = tmp_path / ".secret_key"
    with patch("src.crypto._KEY_PATH", key_file):
        from src.crypto import decrypt, encrypt

        encrypted = encrypt("")
        assert decrypt(encrypted) == ""


def test_different_keys_cannot_decrypt(tmp_path):
    """다른 키로는 복호화할 수 없어야 한다 (빈 문자열 반환)."""
    key_file_1 = tmp_path / "key1"
    key_file_2 = tmp_path / "key2"

    with patch("src.crypto._KEY_PATH", key_file_1):
        from src.crypto import encrypt

        encrypted = encrypt("secret")

    with patch("src.crypto._KEY_PATH", key_file_2):
        from src.crypto import decrypt

        assert decrypt(encrypted) == ""


def test_key_file_created_with_owner_only_permissions(tmp_path):
    """키 파일은 생성 시점부터 0o600(소유자만 읽기/쓰기)이어야 한다 (umask로 인한 세계-읽기 노출 방지)."""
    key_file = tmp_path / ".secret_key"
    with patch("src.crypto._KEY_PATH", key_file):
        from src.crypto import _load_or_create_key

        _load_or_create_key()

    mode = stat.S_IMODE(key_file.stat().st_mode)
    assert mode == 0o600


def test_concurrent_key_creation_is_consistent(tmp_path):
    """회귀 테스트: 여러 스레드가 동시에 키를 생성해도 모두 같은 키를 봐야 한다 (경쟁 시 값 유실 방지)."""
    key_file = tmp_path / ".secret_key"
    results: list[bytes] = []
    lock = threading.Lock()
    n = 16
    barrier = threading.Barrier(n)

    with patch("src.crypto._KEY_PATH", key_file):
        from src.crypto import _load_or_create_key

        def worker():
            barrier.wait()  # 모든 스레드를 동시에 출발시켜 생성 경쟁을 강제한다
            key = _load_or_create_key()
            with lock:
                results.append(key)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert len(results) == n
    assert b"" not in results  # 생성 직후·쓰기 전 빈 키를 읽으면 안 됨
    assert len(set(results)) == 1  # 모든 스레드가 동일한 키를 사용해야 함


def test_directory_key_path_warns_once(tmp_path, caplog):
    """회귀 테스트: .secret_key가 디렉토리일 때 경고는 매 암복호화가 아니라 1회만 남겨야 한다."""
    key_dir = tmp_path / ".secret_key"
    key_dir.mkdir()

    import src.crypto as crypto

    with patch("src.crypto._KEY_PATH", key_dir):
        crypto._dir_warning_emitted = False
        crypto._fernet_for.cache_clear()
        with caplog.at_level("WARNING"):
            encrypted = crypto.encrypt("secret")
            for _ in range(5):
                assert crypto.decrypt(encrypted) == "secret"

    dir_warnings = [r for r in caplog.records if "디렉토리입니다" in r.message]
    assert len(dir_warnings) == 1
    crypto._fernet_for.cache_clear()


def test_fernet_instance_is_cached_per_path(tmp_path):
    """회귀 테스트: 같은 키 경로에서는 Fernet 인스턴스를 재사용해 디스크 재읽기를 피해야 한다."""
    key_file = tmp_path / ".secret_key"

    import src.crypto as crypto

    with patch("src.crypto._KEY_PATH", key_file):
        crypto._fernet_for.cache_clear()
        first = crypto._fernet()
        second = crypto._fernet()
        assert first is second
    crypto._fernet_for.cache_clear()


def test_decrypt_invalid_token_logs_warning(tmp_path, caplog):
    """회귀 테스트: 복호화 실패(InvalidToken)는 조용히 넘어가지 말고 경고 로그를 남겨야 한다."""
    key_file_1 = tmp_path / "key1"
    key_file_2 = tmp_path / "key2"

    with patch("src.crypto._KEY_PATH", key_file_1):
        from src.crypto import encrypt

        encrypted = encrypt("secret")

    with patch("src.crypto._KEY_PATH", key_file_2), caplog.at_level("WARNING"):
        from src.crypto import decrypt

        assert decrypt(encrypted) == ""

    assert any("복호화 실패" in record.message for record in caplog.records)
