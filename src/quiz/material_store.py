"""업로드된 강의자료 파일 저장소.

구조: {download_dir}/materials/{course}/{week}/{filename}
material_id는 요약 저장소와 동일하게 절대경로를 urlsafe base64로 인코딩한 값이다.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from src.quiz.materials import SUPPORTED_SUFFIXES


def _sanitize_segment(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*]', "", value or "")
    value = re.sub(r"\.{2,}", "", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    return value or "unknown"


def _week_dir(week_label: str) -> str:
    m = re.match(r"(\d+주차)", week_label or "")
    return m.group(1) if m else _sanitize_segment(week_label or "") or "기타"


def materials_root(download_dir: str) -> Path:
    return (Path(download_dir) / "materials").expanduser().resolve()


def _is_allowed(path: Path, download_dir: str) -> bool:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return False
    try:
        path.resolve().relative_to(materials_root(download_dir))
        return True
    except ValueError:
        return False


def encode_material_id(path: Path) -> str:
    raw = str(path.expanduser().resolve()).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_material_id(material_id: str, download_dir: str) -> Path:
    try:
        padded = material_id + "=" * (-len(material_id) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        path = Path(raw).expanduser().resolve()
    except Exception as e:
        raise ValueError("잘못된 자료 ID입니다.") from e
    if not _is_allowed(path, download_dir):
        raise ValueError("허용되지 않은 자료 파일입니다.")
    return path


def save_material(
    *, download_dir: str, course_name: str, week_label: str, filename: str, data: bytes
) -> dict[str, Any]:
    """업로드 파일을 저장하고 메타데이터를 반환한다."""
    name = _sanitize_segment(Path(filename).name)
    if Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("지원하지 않는 형식입니다. PDF / PPTX / DOCX만 가능합니다.")

    target_dir = materials_root(download_dir) / _sanitize_segment(course_name) / _week_dir(week_label)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name

    # 동일 이름 존재 시 (1), (2) … 접미사.
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 1
        while target.exists():
            target = target_dir / f"{stem} ({i}){suffix}"
            i += 1

    target.write_bytes(data)
    return _material_meta(target, download_dir, course_name, week_label)


def _material_meta(path: Path, download_dir: str, course_name: str = "", week_label: str = "") -> dict[str, Any]:
    root = materials_root(download_dir)
    rel_parts = path.resolve().relative_to(root).parts if path.resolve().is_relative_to(root) else ()
    return {
        "id": encode_material_id(path),
        "name": path.name,
        "course": course_name or (rel_parts[0] if len(rel_parts) > 0 else ""),
        "week": week_label or (rel_parts[1] if len(rel_parts) > 1 else ""),
        "size": path.stat().st_size if path.is_file() else 0,
        "suffix": path.suffix.lower(),
    }


def list_materials(*, download_dir: str, course_name: str | None = None) -> list[dict[str, Any]]:
    root = materials_root(download_dir)
    if not root.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        meta = _material_meta(path, download_dir)
        if course_name and meta["course"] != _sanitize_segment(course_name):
            continue
        items.append(meta)
    return items


def get_material_path(material_id: str, download_dir: str) -> Path:
    path = decode_material_id(material_id, download_dir)
    if not path.is_file():
        raise FileNotFoundError("자료 파일을 찾을 수 없습니다.")
    return path


def delete_material(material_id: str, download_dir: str) -> bool:
    path = decode_material_id(material_id, download_dir)
    if path.is_file():
        path.unlink()
        return True
    return False
