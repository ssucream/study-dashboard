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

# 스크래핑(과목 목록 로딩)과 Playwright 브라우저 재시작을 전역에서 직렬화하는 뮤텍스.
# ensure_courses_loaded / refresh_courses / 자동 모드 사이클이 모두 이 락을 잡는다.
# 자동 사이클의 브라우저 close()가 진행 중인 fetch_all_details를 끊어
# 과목 상세가 None이 되던 레이스를 방지한다. (재생 자체는 락을 잡지 않는다)
scraper_lock = asyncio.Lock()
