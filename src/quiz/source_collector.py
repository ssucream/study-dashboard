"""문제 생성용 컨텍스트 조립.

선택된 시험범위 주차의 (1) AI 요약본, (2) STT 원본, (3) 업로드 강의자료 텍스트를
모아 하나의 프롬프트 컨텍스트 문자열로 병합한다. 요약본/STT 위치는 기존 저장 규칙을
그대로 재사용한다:

  - 요약본: backend.api.summary_store.find_summary_path
  - STT:    src.downloader.pipeline.build_download_paths 의 txt 경로
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 프롬프트에 실을 컨텍스트 총 문자 수 상한. 주차를 많이 선택하면 초과하므로
# 주차별로 균등하게 잘라낸다.
DEFAULT_MAX_CONTEXT_CHARS = 120_000


@dataclass
class LectureText:
    week_label: str
    title: str
    summary_text: str = ""
    stt_text: str = ""

    @property
    def has_any(self) -> bool:
        return bool(self.summary_text.strip() or self.stt_text.strip())


@dataclass
class MaterialText:
    name: str
    text: str


@dataclass
class ContextResult:
    context: str
    stats: dict[str, Any] = field(default_factory=dict)


def _read_text(path: Path | None) -> str:
    if not path or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def gather_lecture_texts(
    *,
    term: str,
    course_name: str,
    download_dir: str,
    lectures: list[tuple[str, str]],
) -> list[LectureText]:
    """(week_label, lecture_title) 목록에 대해 요약본 + STT 텍스트를 읽어온다."""
    from backend.api.summary_store import find_summary_path

    from src.downloader.pipeline import build_download_paths

    results: list[LectureText] = []
    for week_label, title in lectures:
        summary_path = find_summary_path(term, course_name, week_label, title)
        try:
            _base, _mp4, _mp3, txt_path, _summary = build_download_paths(
                download_dir=download_dir,
                course_name=course_name,
                week_label=week_label,
                lecture_title=title,
            )
        except ValueError:
            txt_path = None
        results.append(
            LectureText(
                week_label=week_label,
                title=title,
                summary_text=_read_text(summary_path),
                stt_text=_read_text(txt_path),
            )
        )
    return results


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n…(이하 생략)", True


def build_context(
    *,
    week_labels: list[str],
    lecture_texts: list[LectureText],
    materials: list[MaterialText] | None = None,
    include_stt: bool = True,
    max_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> ContextResult:
    """수집한 텍스트를 주차·출처 라벨이 붙은 하나의 컨텍스트로 병합한다."""
    materials = materials or []

    # 주차별 블록 구성.
    blocks: list[str] = []
    by_week: dict[str, list[LectureText]] = {}
    for lt in lecture_texts:
        by_week.setdefault(lt.week_label, []).append(lt)

    used_summary = 0
    used_stt = 0
    ordered_weeks = week_labels or sorted(by_week)

    # 주차·자료 전체에 걸쳐 균등 예산을 배분한다.
    material_budget = min(max_chars, sum(len(m.text) for m in materials))
    lecture_budget = max(0, max_chars - material_budget)
    per_week = lecture_budget // max(1, len(ordered_weeks))
    truncated = False

    for week in ordered_weeks:
        items = by_week.get(week, [])
        if not items:
            continue
        per_lecture = per_week // max(1, len(items))
        week_lines = [f"### [주차] {week}"]
        for lt in items:
            if lt.summary_text.strip():
                chunk, cut = _truncate(lt.summary_text, per_lecture)
                truncated = truncated or cut
                week_lines.append(f"#### [요약본] {lt.title}\n{chunk}")
                used_summary += 1
            if include_stt and lt.stt_text.strip():
                chunk, cut = _truncate(lt.stt_text, per_lecture)
                truncated = truncated or cut
                week_lines.append(f"#### [강의 전사(STT)] {lt.title}\n{chunk}")
                used_stt += 1
        if len(week_lines) > 1:
            blocks.append("\n\n".join(week_lines))

    for m in materials:
        if not m.text.strip():
            continue
        chunk, cut = _truncate(m.text, material_budget // max(1, len(materials)))
        truncated = truncated or cut
        blocks.append(f"### [업로드 자료] {m.name}\n{chunk}")

    context = "\n\n---\n\n".join(blocks)
    return ContextResult(
        context=context,
        stats={
            "weeks": ordered_weeks,
            "summary_lectures": used_summary,
            "stt_lectures": used_stt,
            "materials": len([m for m in materials if m.text.strip()]),
            "context_chars": len(context),
            "truncated": truncated,
        },
    )
