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
- **LAN 노출**: 의도적으로 그대로 둠 — 서버와 접속 PC가 분리된 환경이라 LAN 접근이 필요조건.
  사용자 확인 완료 (재검토 시 다시 이슈화하지 말 것)

---

## MEDIUM

### 보안
- **의존성 취약점**: `starlette 1.0.0`(PYSEC-2026-161/-248/-2280/-249 등 6건),
  `cryptography 46.0.5`(PYSEC-2026-36, `Hash.update()` 버퍼오버플로) — `uv lock --upgrade-package starlette --upgrade-package cryptography` 로 갱신 검토.
  다른 outdated 패키지: `requests`, `urllib3`, `idna`, `pyasn1`, `pygments`, `setuptools`, `click`.
- **학번 평문 로깅 불일치**: `backend/api/routes/auth.py:108` — 로그인 *성공* 경로만 `event_log.mask_user_id()`를
  안 쓰고 원문 학번을 기록. 실패 경로(64, 78, 89행)는 이미 마스킹 중이라 일관성만 맞추면 됨.
- **`esc()`가 따옴표 미이스케이프**: `frontend/js/utils.js:5-7` — `< > &`만 이스케이프, `"`/`'` 안 함.
  `app.js:378`에서 HTML attribute 안에 쓰이지만 현재는 `_sanitize_filename()`이 `"`를 걸러줘서
  실제 익스플로잇은 안 됨. 두 sanitizer 중 하나만 바뀌어도 깨질 수 있는 fragile 상태.

### 프론트엔드 UX
- **검색창 타이핑 중 다운로드 진행 폴링 고아화**: `frontend/js/app.js:498, 752, 1245` — `#filter-query`
  입력마다 강의 목록을 재렌더링해 진행 중인 다운로드 row/버튼이 detached됨 → 같은 강의 중복 다운로드 가능.
- **두 폴링 루프가 `#player-message-log`를 두고 경쟁**: `app.js:865` vs `:195` — auto-download 진행률 표시가
  재생 상태 WS 업데이트(2초 주기)에 계속 지워짐. 별도 엘리먼트로 분리 필요.
- **로그아웃 후 폴링이 안 멈춤**: `app.js:51, 155` — `showLogin()`이 `state.userId`를 안 지워서 WS `onclose`
  콜백이 로그인 화면에서도 `setInterval` 폴링을 계속 돌림.
- **요약 폴링 체인이 로그아웃 시 취소 안 됨**: `app.js:773, 824` — `state.downloadTaskTimers`에 등록 안 된
  재귀 `setTimeout`이라 `stopAllDownloadTaskPolling()`이 못 잡음.

### 백엔드 생명주기
- **서버 종료 시 실행 중 task 미취소**: `backend/main.py:35` (lifespan) — `docker compose down` 중 다운로드가
  있으면 task 이력이 안 남고, 부분 mp4가 `exists: true`로 잘못 보고됨.
- **로그아웃 시 scraper 정리 중 실행 중 task가 AttributeError**: `backend/api/routes/auth.py:51, 129` —
  `scraper=None` 처리 후 살아있는 task가 `app_state.scraper._page`를 참조하면 원본 에러가 그대로 노출됨.
- **`stop_play`가 취소 완료 전에 `is_playing=False`**: `backend/api/routes/player.py:369` — `task_manager.cancel`이
  3초 뒤 포기해도 곧바로 재생 가능 상태가 돼서, Playwright 정리가 안 끝난 채 재생 재시도 가능.
- **다운로드 Config 매핑 3중 중복 + 드리프트**: `tasks.py:97-117` / `player.py:120-140` / `auto.py:84-104` —
  이미 `tasks.py`는 `ai_api_key=Config.GOOGLE_API_KEY`, 나머지 둘은 `... or ""`로 갈라짐.
  `src/downloader/pipeline.py`에 `run_download_from_config(...)` 헬퍼로 통합 권장.

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
