"""파일 기반 강의 요약 조회 헬퍼."""

import base64
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config import Config, get_data_path
from src.downloader.video_downloader import make_filepath

_ALLOWED_SUMMARY_SUFFIXES = {".md", ".txt"}


def summaries_dir() -> Path:
    """요약 대시보드용 canonical 저장 디렉터리를 반환한다."""
    return get_data_path("summaries")


def _safe_term(term: str) -> str:
    """학기명을 경로 segment로 안전하게 정규화한다."""
    value = re.sub(r'[<>:"/\\|?*]', "", term or "")
    value = re.sub(r"\.{2,}", "", value).strip(" .")
    return value or "unknown-term"


@lru_cache(maxsize=8)
def _resolved_roots(summaries_root: str, download_dir: str) -> tuple[Path, ...]:
    """summaries_dir()/download_dir 문자열 조합별로 expanduser().resolve() 결과를 캐시한다.

    입력 조합을 키로 쓰기 때문에 테스트에서 summaries_dir()를 tmp_path로 monkeypatch해도
    이전 테스트의 캐시가 새 경로를 가리는 문제가 없다.
    """
    roots = [Path(summaries_root)]
    if download_dir:
        roots.append(Path(download_dir))

    unique_roots: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_roots.append(resolved)
    return tuple(unique_roots)


def _summary_roots() -> list[Path]:
    """요약 파일 접근을 허용할 root 목록.

    새 요약 대시보드 저장소(data/summaries)와 기존 CLI 요약 위치(DOWNLOAD_DIR)를
    함께 허용해, 웹 요약 저장 포맷 도입 전 생성된 `_summarized.txt`도 볼 수 있게 한다.
    """
    return list(_resolved_roots(str(summaries_dir()), Config.get_download_dir()))


def _canonical_summary_path(term: str, course_name: str, week_label: str, lecture_title: str) -> Path:
    rel = make_filepath(course_name, week_label, lecture_title).with_suffix(".md")
    return (summaries_dir() / _safe_term(term) / rel).expanduser().resolve()


def _legacy_summary_path(course_name: str, week_label: str, lecture_title: str) -> Path:
    mp4_path = Path(Config.get_download_dir()) / make_filepath(course_name, week_label, lecture_title)
    return mp4_path.with_stem(mp4_path.stem + "_summarized").with_suffix(".txt").expanduser().resolve()


def _is_allowed_summary_path(path: Path) -> bool:
    if path.suffix.lower() not in _ALLOWED_SUMMARY_SUFFIXES:
        return False
    for root in _summary_roots():
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _encode_summary_id(path: Path) -> str:
    raw = str(path.expanduser().resolve()).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_summary_id(summary_id: str) -> Path:
    try:
        padded = summary_id + "=" * (-len(summary_id) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        path = Path(raw).expanduser().resolve()
    except Exception as e:
        raise ValueError("잘못된 요약 ID입니다.") from e

    if not _is_allowed_summary_path(path):
        raise ValueError("허용되지 않은 요약 파일입니다.")
    return path


def find_summary_path(term: str, course_name: str, week_label: str, lecture_title: str) -> Path | None:
    """강의 정보에 해당하는 생성된 요약 파일을 찾는다."""
    candidates = [
        _canonical_summary_path(term, course_name, week_label, lecture_title),
        _legacy_summary_path(course_name, week_label, lecture_title),
    ]
    for candidate in candidates:
        if candidate.is_file() and _is_allowed_summary_path(candidate):
            return candidate
    return None


def summary_for_lecture(term: str, course_name: str, week_label: str, lecture_title: str) -> dict[str, Any]:
    """강의 row에서 사용할 요약 가용성 메타데이터를 반환한다."""
    path = find_summary_path(term, course_name, week_label, lecture_title)
    if not path:
        return {"available": False, "id": None}
    return {
        "available": True,
        "id": _encode_summary_id(path),
        "format": "markdown" if path.suffix.lower() == ".md" else "text",
    }


def encode_summary_id(path: Path) -> str:
    """요약 파일 경로를 공유 가능한 ID로 인코딩한다."""
    return _encode_summary_id(path)


def list_summaries() -> list[dict[str, Any]]:
    """저장된 요약 파일 목록을 반환한다.

    canonical 저장 구조: summaries/{term}/{course}/{week}/{title}.md
    """
    items: list[dict[str, Any]] = []
    sdir = summaries_dir()
    if not sdir.exists():
        return items

    for path in sorted(sdir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _ALLOWED_SUMMARY_SUFFIXES:
            continue
        if not _is_allowed_summary_path(path):
            continue
        try:
            rel = path.relative_to(sdir)
            parts = rel.parts
            # 구조: term / course / week / title.md (4 segments)
            if len(parts) < 4:
                continue
            term = parts[0]
            course = parts[1]
            week = parts[2]
            title = path.stem
        except Exception:
            continue
        items.append(
            {
                "id": _encode_summary_id(path),
                "term": term,
                "course": course,
                "week": week,
                "title": title,
                "format": "markdown" if path.suffix.lower() == ".md" else "text",
            }
        )
    return items


def read_summary(summary_id: str) -> dict[str, Any]:
    """요약 ID로 파일 내용을 읽는다."""
    path = _decode_summary_id(summary_id)
    if not path.is_file():
        raise FileNotFoundError("요약 파일을 찾을 수 없습니다.")

    content = path.read_text(encoding="utf-8")
    title = path.stem.removesuffix("_summarized")
    return {
        "id": summary_id,
        "title": title,
        "content": content,
        "format": "markdown" if path.suffix.lower() == ".md" else "text",
    }
