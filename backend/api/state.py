import asyncio
from dataclasses import dataclass, field


@dataclass
class PlaybackProgress:
    current: float = 0.0
    duration: float = 0.0
    ended: bool = False
    error: str | None = None
    status: str = "idle"
    log_path: str | None = None
    refresh_recommended: bool = False  # 완료 갱신 실패 시 새로고침 안내 (1.3)

    @property
    def progress_pct(self) -> float:
        if self.duration <= 0:
            return 0.0
        return min(100.0, self.current / self.duration * 100)


@dataclass
class AutoModeState:
    enabled: bool = False
    schedule_hours: list = field(default_factory=lambda: [9, 13, 18, 23])
    task: asyncio.Task | None = None
    task_id: str | None = None
    current_course: str = ""
    current_lecture: str = ""
    processed_count: int = 0
    next_run_at: str = ""
    error: str | None = None
    pipeline_stage: str = ""  # 재생 후 파이프라인 단계 메시지 (다운로드/STT/요약/텔레그램)


@dataclass
class AppState:
    scraper: object = None  # CourseScraper (순환 import 방지를 위해 Any)
    user_id: str = ""
    courses: list = field(default_factory=list)
    details: list = field(default_factory=list)
    is_playing: bool = False
    current_lecture_title: str = ""
    current_lecture_url: str = ""
    current_week_label: str = ""
    current_course_name: str = ""
    current_course_id: str = ""
    playback: PlaybackProgress = field(default_factory=PlaybackProgress)
    play_task: asyncio.Task | None = None
    play_task_id: str | None = None
    auto: AutoModeState = field(default_factory=AutoModeState)
    auto_download_task_id: str | None = None


app_state = AppState()
