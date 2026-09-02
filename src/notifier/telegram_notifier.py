"""
텔레그램 봇 알림 모듈.

재생 완료 알림과 AI 요약 결과 전송 기능을 제공한다.
"""

from pathlib import Path


def _send_message(bot_token: str, chat_id: str, text: str) -> bool:
    """텔레그램 메시지를 전송한다. 응답 body의 ok 필드로 성공 여부를 판정한다."""
    import requests

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            return data.get("ok", False)
        return False
    except Exception:
        return False


def _send_document(bot_token: str, chat_id: str, file_path: Path, caption: str = "") -> bool:
    """텔레그램 파일을 전송한다. 응답 body의 ok 필드로 성공 여부를 판정한다."""
    import sys

    import requests

    def _err(msg: str) -> None:
        print(f"[Telegram][ERROR] {msg}", file=sys.stderr, flush=True)

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    try:
        if not file_path.exists():
            _err(f"파일 없음: {file_path}")
            return False
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb > 50:
            _err(f"파일 크기 초과 ({file_size_mb:.1f} MB > 50 MB): {file_path}")
            return False
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (file_path.name, f)},
                timeout=60,
            )
        if resp.ok:
            data = resp.json()
            if not data.get("ok", False):
                _err(f"sendDocument API 오류: {data}")
            return data.get("ok", False)
        _err(f"sendDocument HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        _err(f"sendDocument 예외: {e}")
        return False


def _lecture_label(course_name: str, week_label: str, lecture_title: str) -> str:
    """'과목-주차 강의명' 형식의 레이블을 반환한다."""
    parts = []
    if course_name:
        parts.append(course_name)
    if week_label:
        parts.append(week_label)
    prefix = "-".join(parts)
    if prefix:
        return f"{prefix} {lecture_title}"
    return lecture_title


def notify_playback_complete(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
) -> bool:
    """영상 재생 완료 알림을 전송한다."""
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[알림] {label} 시청을 완료하였습니다."
    return _send_message(bot_token, chat_id, text)


def notify_playback_error(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
    failed: bool = True,
) -> bool:
    """영상 재생 실패 또는 미완료 알림을 전송한다.

    Args:
        failed: True면 '재생을 실패', False면 '재생을 완료하지 못함'
    """
    label = _lecture_label(course_name, week_label, lecture_title)
    if failed:
        text = f"[오류] {label} 재생을 실패하였습니다."
    else:
        text = f"[오류] {label} 재생을 완료하지 못하였습니다."
    return _send_message(bot_token, chat_id, text)


def notify_download_error(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
) -> bool:
    """다운로드 실패 알림을 전송한다."""
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[오류] {label} 다운로드에 실패하였습니다."
    return _send_message(bot_token, chat_id, text)


def notify_download_unsupported(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
) -> bool:
    """다운로드 불가 강의 알림을 전송한다."""
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[안내] {label} 은(는) 다운로드가 지원되지 않는 강의입니다."
    return _send_message(bot_token, chat_id, text)


def notify_auto_error(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
    error_msg: str,
) -> bool:
    """자동 모드 처리 오류 알림을 전송한다."""
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[자동 모드 오류] {label}\n{error_msg}"
    return _send_message(bot_token, chat_id, text)


def notify_summary_complete(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
    summary_text: str,
    summary_path: Path,
    auto_delete_files: list[Path] | None = None,
) -> bool:
    """AI 요약 완료 알림을 전송한다. 요약 내용을 메시지로, 파일도 함께 첨부한다.
    전송 성공 시 auto_delete_files에 포함된 파일을 삭제한다.
    """
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[알림] {label}의 요약 내용을 다음과 같이 제공해드립니다.\n\n{summary_text}"

    # 요약 내용 텍스트 메시지 전송 (4096자 초과 시 잘라서 전송)
    _MAX = 4096
    chunks = [text[i : i + _MAX] for i in range(0, len(text), _MAX)]
    msg_ok = all(_send_message(bot_token, chat_id, chunk) for chunk in chunks)

    # 요약 파일 첨부 전송
    file_ok = _send_document(bot_token, chat_id, summary_path, caption=f"{label} 요약 파일")

    success = msg_ok and file_ok

    if success and auto_delete_files:
        for path in auto_delete_files:
            try:
                if path and Path(path).exists():
                    Path(path).unlink()
            except Exception:
                pass

    return success


def notify_deadline_warning(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
    type_label: str,
    end_date: str,
    remaining_hours: float,
) -> bool:
    """마감 임박 알림을 전송한다."""
    if remaining_hours >= 48:
        time_text = f"약 {int(remaining_hours // 24)}일 남음"
    elif remaining_hours >= 1:
        time_text = f"약 {int(remaining_hours)}시간 남음"
    else:
        time_text = f"약 {int(remaining_hours * 60)}분 남음"
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[마감 임박] {label}\n{type_label} | 마감: {end_date} ({time_text})"
    return _send_message(bot_token, chat_id, text)


def notify_summary_send_error(
    bot_token: str,
    chat_id: str,
    course_name: str,
    week_label: str,
    lecture_title: str,
) -> bool:
    """요약 내용 발송 실패 알림을 전송한다."""
    label = _lecture_label(course_name, week_label, lecture_title)
    text = f"[오류] {label} 요약 내용 발송에 실패하였습니다."
    return _send_message(bot_token, chat_id, text)


def verify_bot(bot_token: str, chat_id: str) -> tuple[bool, str]:
    """봇 토큰과 chat ID가 유효한지 확인하고 테스트 메시지를 전송한다.

    Returns:
        (성공 여부, 오류 메시지 또는 빈 문자열)
    """
    import requests

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=10,
        )
        if not resp.ok:
            data = resp.json()
            desc = data.get("description", resp.text)
            return False, f"봇 토큰 오류: {desc}"
        bot_name = resp.json().get("result", {}).get("username", "")
    except Exception as e:
        return False, f"네트워크 오류: {e}"

    ok = _send_message(bot_token, chat_id, f"[알림] study-helper 텔레그램 알림이 연결되었습니다! (봇: @{bot_name})")
    if not ok:
        return False, "메시지 전송 실패. Chat ID를 확인하세요."

    return True, ""
