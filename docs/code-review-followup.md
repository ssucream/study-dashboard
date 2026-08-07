# 코드 리뷰 후속 작업 목록

2026-08-06 3-agent 전체 코드 리뷰(backend/frontend, src, security)에서 나온 항목 중
CRITICAL 1건과 HIGH 10건은 처리 완료(`v26.7.0`, `v26.7.1`). 아래는 아직 남은 항목이다.

## 완료된 항목 (참고용, 재작업 불필요)

- CRITICAL: `tasks.py` `build_download_paths()` 5-tuple 언패킹 누락
- HIGH: SSRF(`lecture_url` 허용목록), 요약 저장 경로 분산, 다운로드 이벤트 루프 블로킹,
  Whisper 캐시 레이스, 동시 다운로드 가드, `/data`→`/db` 경로 불일치, 수동 재생 auto 가드,
  `refresh_courses` 가드, `.secret_key` 생성 경쟁/무음 실패, `/ws/status` 인증
- HIGH(놓쳤던 항목): 설정 미비 배너 permanent false positive —
  `GET /api/settings`에 `HAS_GOOGLE_API_KEY`/`HAS_TELEGRAM_BOT_TOKEN` boolean 추가,
  프론트는 그 값으로 배너 조건 판단하도록 변경 (`backend/api/routes/settings.py`, `frontend/js/app.js`)
- MEDIUM(백엔드 안정성 7건, 2026-08-07 처리):
  - ffmpeg subprocess timeout(`audio_converter.py`, 30분 + `TimeoutExpired` → `RuntimeError`)
  - 파이프라인 부분 실패 시 파일 정보 유실(`pipeline.py`의 `PipelineStageError`로 STT/요약 실패 시에도
    이미 완료된 mp4/mp3 정보를 `task_manager.py`/`tasks.py`가 보존하도록 수정)
  - Gemini 빈 응답 TypeError(`summarizer.py`, `response.text is None` 시 명확한 `RuntimeError`)
  - 사용자 프롬프트 템플릿 `str.format` KeyError 위험(`summarizer.py`, `template.replace("{text}", text)`로 교체)
  - 로거 FileHandler 미종료(`logger.py`에 `close_error_logger()` 추가, 6개 호출부 적용)
  - `Config.load()` DB 스키마 재생성 반복(`db.py`, 경로별 캐싱으로 최초 1회만 `_ensure_schema()`)
  - DB 경로 CWD 의존(`db.py`, `/db` 없을 때 `Path(__file__)` 기준 절대경로로 폴백)
  - 회귀 테스트 7건 추가(`test_converter.py`/`test_summarizer.py`/`test_download_pipeline.py`/`test_db.py`/`test_logger.py`), 전체 111개 통과
- MEDIUM(보안 3건, 2026-08-07 처리):
  - 의존성 취약점: `starlette` 1.0.0→1.4.1, `cryptography` 46.0.5→50.0.0로 scoped 업그레이드
    (`requests`/`urllib3`/`idna`/`pyasn1`/`pygments`/`setuptools`/`click`도 patch/minor 범위로 함께 갱신).
    `fastapi`/`google-genai`/`rich`/`playwright` 등 major 버전이 걸린 패키지는 회귀 위험이 커서
    이번 범위에서 제외 — 필요 시 별도로 검토.
  - 학번 평문 로깅 불일치(`backend/api/routes/auth.py:108`, 로그인 성공 경로도 `mask_user_id()` 적용)
  - `esc()` 따옴표 미이스케이프(`frontend/js/utils.js`, `&quot;`/`&#39;` 추가 이스케이프)
  - 회귀 테스트: 기존 `test_web_auth.py` 마스킹 검증으로 갱신, 전체 111개 통과, ruff 클린
- MEDIUM(프론트엔드 UX 4건 + 백엔드 생명주기 4건, 2026-08-07 처리):
  - 검색창 타이핑 중 다운로드 진행 폴링 고아화: `state.activeDownloads`(url→taskId)로 추적,
    `_renderCourseWeeks` 재렌더링 시 진행 중인 다운로드를 새 row/버튼에 다시 붙임 (`app.js`)
  - 두 폴링 루프가 `#player-message-log`를 두고 경쟁: 자동 다운로드 진행률 표시를
    `#player-auto-download-log`로 분리 (`index.html`, `app.js`)
  - 로그아웃 후 폴링이 안 멈춤: `showLogin()`이 `state.userId`를 비워 WS `onclose`의
    재폴링 재시작 조건을 차단 (`app.js`)
  - 요약 폴링 체인이 로그아웃 시 취소 안 됨: 재귀 `setTimeout`을 `state.downloadTaskTimers`에
    등록해 `stopAllDownloadTaskPolling()`이 잡을 수 있게 함 (`app.js`)
  - 서버 종료 시 실행 중 task 미취소: lifespan 종료 시 미완료 task를 `task_manager.cancel()`로
    일괄 취소해 부분 상태 방치 방지 (`backend/main.py`)
  - 로그아웃 시 scraper 정리 중 AttributeError: play/auto task가 아직 실행 중이면 scraper
    close/None을 건너뛰고 다음 로그인이 정리하도록 미룸 (`auth.py`)
  - `stop_play`가 취소 완료 전 `is_playing=False`: 중복 대입 제거, `run()`의 `finally`를
    유일한 신뢰 소스로 둠 (`player.py`)
  - 다운로드 Config 매핑 3중 중복+드리프트: `pipeline.py`에 `run_download_from_config()` 헬퍼
    추가, `tasks.py`/`player.py`/`auto.py` 세 호출부를 이 헬퍼 호출로 통합
  - 회귀 테스트 4건 추가(`test_web_player.py`/`test_web_auth.py`/신규 `test_main_lifespan.py`),
    전체 114개 통과, ruff 클린
- **LAN 노출**: 의도적으로 그대로 둠 — 서버와 접속 PC가 분리된 환경이라 LAN 접근이 필요조건.
  사용자 확인 완료 (재검토 시 다시 이슈화하지 말 것)

---

## LOW (급하지 않음)

- `backend/api/routes/courses.py:186-196` — `_summary_roots()`가 매 호출마다 `expanduser().resolve()` 재계산,
  `lru_cache` 하나로 해결.
- `backend/api/routes/courses.py:21` — `_summaries_dir()`가 `summary_store.summaries_dir()`와 로직 중복. 삭제하고 import로 대체.
- `_require_auth()`가 8개 라우트 파일에 복붙됨 — FastAPI dependency 하나로 추출 가능.
- `frontend/js/app.js:184` — `$('#player-pct').childNodes[0].textContent`가 첫 child가 텍스트 노드라는 가정에 의존, fragile.
- `src/downloader/pipeline.py:72` — `except (ValueError, Exception)`이 모든 예외를 `{"exists": False}`로 은폐, 디버깅 불가.
- `src/downloader/video_downloader.py:317` — `iter_content` 중 예외 시 `requests` 응답 미종료, 소켓 누수.
- `src/player/background_player.py:1119-1121` — `CancelledError`를 삼키고 문자열 비교(`"사용자 중단"`)로 취소를 전파, 문구 바뀌면 조용히 깨짐.
- `tasks.py`(600줄+)/`app.js`(1250줄+) — 지금 쪼갤 정도는 아니지만 더 커지면 분리 검토
  (`app.js`는 course-detail 렌더링 + task 폴링 구간, 약 500줄이 분리 후보).

## 확인 필요 (낮은 확신도)

- **재개 다운로드 파일 손상 가능성**: `src/downloader/video_downloader.py:312-322` — Range 헤더로 이어받기 중
  CDN 서명 URL이 만료되면 서버가 다른 콘텐츠로 206을 줄 수 있어 mp4가 손상될 수 있음.
  실제 Learning X CDN이 어떻게 동작하는지 확인 필요 (재현 전까지는 추정).
