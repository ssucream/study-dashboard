# study-dashboard: Learning X 백그라운드 학습 도구

숭실대학교 Canvas Learning X(canvas.ssu.ac.kr)의 강의 영상을 Docker 컨테이너 기반 웹 대시보드에서
백그라운드로 재생(출석 처리)하거나 다운로드/변환/요약할 수 있는 도구.

## 실행 방법

`docker-compose.yml` 기본값은 Docker Hub 이미지(`igor0670/study-dashboard-{backend,frontend}`)를
pull해서 실행하는 배포용 구성이다. 소스를 직접 수정하며 개발할 때는 각 서비스의 `image:` 줄을
지우고 바로 아래 주석 처리된 `build:` 섹션(및 소스 볼륨 마운트)의 주석을 해제해야 한다.

```bash
# 배포용 (기본값) — Docker Hub 이미지 pull
docker compose pull
docker compose up -d

# 로컬 개발용 — docker-compose.yml의 image:를 build:로 먼저 교체한 뒤
docker compose up --build      # 이미지 재빌드 후 실행
```

- 웹 대시보드(backend + frontend 2서비스) 구조이므로 `run --rm`이 아니라 `up`으로 실행
- 로컬 빌드 모드로 전환 시 `src/`, `backend/`, `frontend/index.html`·`js/`·`nginx.conf`를 볼륨 마운트하면
  코드 수정 후 재빌드 없이 반영됨 (uvicorn `--reload`)
- 다운로드 파일은 `./downloads/`에 저장됨 (컨테이너 내 `/downloads`)
- 설정 DB는 `./db/app.db`에 영속화됨 (컨테이너 내 `/db/app.db`)
- HTTPS 접속을 위한 자체 서명 인증서가 없으면 `scripts/generate-local-cert.sh`로 최초 1회 생성
- 태그(`v*`) push 시 `.github/workflows/release.yml`이 backend/frontend 이미지를 Docker Hub에 빌드·푸시하고
  GitHub Release를 생성한다. Docker Hub 계정 인증은 저장소 secret(`DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN`)로 처리
- Whisper 모델, Playwright Chromium은 named volume에 캐시되어 재빌드 시 재다운로드 불필요

## 개발 환경 설정

의존성 추가 시 `pyproject.toml` 수정 후 `docker compose build`로 재빌드.

torch는 `pyproject.toml`에 포함하지 않음 — Dockerfile에서 CPU wheel로 직접 설치.

## 절대 건드리면 안 되는 것들

- **Playwright headless Chromium 유지**: 시스템 Chrome 경로 하드코딩 금지. Docker에서는 Playwright 내장 Chromium만 사용.
- **데스크톱 GUI 의존성 추가 금지**: flet, PyQt5 등 데스크톱 GUI 라이브러리 사용 금지. 백엔드는 headless, UI는 웹 대시보드(nginx + 정적 프론트) 전용.
- **비디오 셀렉터**: `video.vc-vplay-video1`로 영상 URL 추출. 변경 시 Learning X 쪽 변경 확인 필요.
- **SQLite 설정 저장소만 사용**: `.env` 기반 설정은 제거됨. 신규 설정 항목은 DB와 웹 설정 화면(`backend/api/routes/settings.py` + `frontend/js/settings.js`)에만 추가할 것.

## 설계 의도

- **STT 엔진**: faster-whisper (CTranslate2 기반, torch 불필요). base 모델이 기본값.
- **요약 엔진**: Gemini / OpenAI / OpenRouter API 중 선택 (provider: `AI_AGENT`). 키는 DB에서 암호화 로드.
- **설정 저장소**: SQLite (`db/app.db`) — `crypto.py`로 민감값 암호화 후 저장.
- **다운로드 경로**: 컨테이너 내 `/downloads` — 기본 compose에서 호스트 `./downloads`를 마운트.
- **출력 파일**: mp4(영상), mp3(음성, ffmpeg 변환), txt(STT 결과), `_summarized.txt`(요약).
- **백그라운드 재생**: video DOM 폴링(Plan A) + 진도 API 직접 호출(Plan B). Plan A 실패 시 자동 전환.
- **자동 모드**: `backend/api/routes/auto.py` — 미완료 강의 일괄 재생 + 스케줄 실행.
- **마감 알림**: `src/notifier/deadline_checker.py` — 로그인 직후 미제출 과제/마감 임박 항목 텔레그램 알림.
- **버전 체크**: `src/updater.py` — 과목 목록 로딩과 병렬로 GitHub 최신 버전 확인.
- **백그라운드 Task**: `backend/api/task_manager.py` — 다운로드/재생/자동모드 Task 생명주기 관리 및 SQLite 영속화.

## 프로젝트 구조

```
study-dashboard/
├── docker-compose.yml                    # backend + frontend 2서비스
├── backend/                              # FastAPI REST/WebSocket API
│   ├── Dockerfile
│   ├── main.py                           # 앱 진입점 — 라우터 등록, lifespan(DB init·Config.load·Task 복원)
│   └── api/
│       ├── auth_dep.py                   # 로그인 세션 의존성
│       ├── state.py                      # 로그인 세션(scraper) 프로세스 싱글턴
│       ├── task_manager.py               # 다운로드/재생/자동모드 Task 생명주기 + SQLite 영속화
│       ├── summary_store.py              # 요약 마크다운 파일 저장/조회
│       ├── validators.py
│       └── routes/
│           ├── auth.py                   # 로그인/로그아웃
│           ├── courses.py                # 과목/주차/강의 목록
│           ├── player.py                 # 단일 강의 재생 + 후처리 파이프라인
│           ├── auto.py                   # 자동 모드 (일괄 재생 + 스케줄)
│           ├── settings.py               # 설정 조회/저장
│           ├── summaries.py              # 요약 열람
│           ├── tasks.py                  # Task 목록/취소/재시도, 다운로드·STT·요약
│           ├── logs.py                   # 행위 로그 조회
│           ├── deadline.py               # 마감 임박 조회/알림
│           └── ws.py                     # WebSocket (진행 상황 push)
├── src/                                  # 도메인 로직 (backend가 import)
│   ├── config.py                         # 설정 로드/저장 (SQLite 기반)
│   ├── crypto.py                         # 계정/API 키 암호화·복호화
│   ├── db.py                             # SQLite 초기화/마이그레이션
│   ├── event_log.py                      # 행위 로그 기록
│   ├── logger.py                         # 로깅 설정 / 스크래퍼 에러 로거
│   ├── updater.py                        # GitHub 최신 버전 확인
│   ├── auth/login.py                     # Playwright 로그인 처리 (SSO 사이트 선택 포함)
│   ├── scraper/
│   │   ├── course_scraper.py             # 과목/주차/강의 목록 스크래핑
│   │   └── models.py                     # Course, LectureItem, Week 등 데이터 모델
│   ├── player/background_player.py       # 백그라운드 재생 (출석용, Plan A/B)
│   ├── downloader/
│   │   ├── pipeline.py                   # 다운로드→변환→STT→요약 파이프라인
│   │   └── video_downloader.py           # 영상 URL 추출 + HTTP 스트리밍 다운로드
│   ├── converter/audio_converter.py      # mp4 → mp3 (ffmpeg)
│   ├── stt/transcriber.py                # faster-whisper STT
│   ├── summarizer/summarizer.py          # Gemini/OpenAI/OpenRouter 요약
│   └── notifier/
│       ├── deadline_checker.py           # 마감 임박 항목 감지
│       └── telegram_notifier.py          # 텔레그램 알림 전송
├── frontend/                             # nginx 정적 서빙 + /api 프록시 (HTTPS)
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── index.html
│   └── js/                               # 바닐라 ES 모듈
│       ├── app.js                        # 진입점, 라우팅, 대시보드
│       ├── api.js                        # fetch 래퍼
│       ├── state.js                      # 클라이언트 상태
│       ├── settings.js                   # 설정 화면
│       ├── modals.js                     # STT/요약 모달
│       ├── summaries.js                  # 요약 열람
│       ├── logs.js                       # 로그 뷰
│       ├── markdown.js                   # 마크다운 렌더러
│       └── utils.js
├── db/app.db                             # 설정 DB (호스트 ./db → 컨테이너 /db 볼륨 마운트)
├── downloads/                            # 다운로드/변환/요약 산출물 (호스트 ./downloads → 컨테이너 /downloads)
├── certs/                                # 로컬 HTTPS 인증서
└── docs/                                 # 기술 문서 (lms-analysis, https-local, telegram-setup)
```

## 설정 DB 스키마 (SQLite)

`db/app.db` — `src/crypto.py`로 민감값 암호화 저장.

```sql
-- 설정 key-value 저장소 (.env 대체)
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 향후 확장 예정
-- download_history: 다운로드 이력
-- lecture_cache:    스크래핑 캐시 (재시작 시 재사용)
```

민감 키 목록 (저장 시 반드시 `encrypt()` 적용):
`GOOGLE_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`

## 설정 항목

| 키 | 설명 | 예시 |
|----|------|------|
| `LMS_USER_ID` | 학번 (메모리 세션 전용, DB 저장 금지) | — |
| `LMS_PASSWORD` | 비밀번호 (메모리 세션 전용, DB 저장 금지) | — |
| `DOWNLOAD_ENABLED` | 영상 다운로드 사용 여부 | `true` / `false` |
| `DOWNLOAD_DIR` | 다운로드 경로 (비워두면 자동) | `/downloads` |
| `DOWNLOAD_RULE` | 다운로드 규칙 | `mp4` / `mp3` / `both` |
| `AUTO_DOWNLOAD_AFTER_PLAY` | 재생 완료 후 자동 다운로드 | `true` / `false` |
| `STT_ENABLED` | STT 사용 여부 | `true` / `false` |
| `STT_LANGUAGE` | STT 언어 | `ko` / `en` / `` (자동) |
| `WHISPER_MODEL` | faster-whisper 모델 | `tiny` / `base` / `small` / `medium` / `large` |
| `AI_ENABLED` | AI 요약 사용 여부 | `true` / `false` |
| `AI_AGENT` | AI 요약 provider | `gemini` / `openai` / `openrouter` |
| `GEMINI_MODEL` | Gemini 모델 ID | `gemini-3.5-flash-lite` |
| `GOOGLE_API_KEY` | Gemini API 키 (암호화) | — |
| `OPENAI_MODEL` | OpenAI 모델 ID | `gpt-5.6-luna` |
| `OPENAI_API_KEY` | OpenAI API 키 (암호화) | — |
| `OPENROUTER_MODEL` | OpenRouter 모델 ID | `openrouter/auto` |
| `OPENROUTER_API_KEY` | OpenRouter API 키 (암호화) | — |
| `SUMMARY_PROMPT_EXTRA` | 요약 프롬프트 추가 지시 (기본 형식 뒤에 덧붙음) | — |
| `CHAPEL_SUMMARY_ENABLED` | 채플 과목 요약에 [강연자 소개]/[성경 말씀] 섹션 자동 추가 | `true` / `false` |
| `TELEGRAM_ENABLED` | 텔레그램 알림 사용 여부 | `true` / `false` |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 (암호화) | — |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | — |
| `TELEGRAM_AUTO_DELETE` | 전송 후 파일 자동 삭제 | `true` / `false` |
| `TELEGRAM_NOTIFY_PLAYBACK` | 재생 완료 알림 발송 여부 | `true` / `false` |
| `TELEGRAM_NOTIFY_SUMMARY` | 요약 내용 발송 여부 | `true` / `false` |
| `TELEGRAM_NOTIFY_ERROR` | 오류 발생 알림 발송 여부 | `true` / `false` |
| `TELEGRAM_NOTIFY_DEADLINE` | 마감 임박 알림 발송 여부 | `true` / `false` |
| `TELEGRAM_DEADLINE_THRESHOLDS` | 마감 알림 발송 시점 (마감 전, 시간 단위 CSV) | `168,72,24,12` |
| `AUTO_ENABLED` | 자동 모드 지속 상태 (앱이 관리, 재로그인 시 복원용) — `자동 모드 중지` 버튼으로만 `false`가 되며 로그아웃·재시작은 유지 | `true` / `false` |
| `AUTO_SCHEDULE_HOURS` | 자동 모드 스케줄 시각 (CSV, 앱이 관리) | `9,13,18,23` |

## Learning X 기술 메모

| 항목 | 값 |
|------|-----|
| 대시보드 URL | `https://canvas.ssu.ac.kr/` |
| 과목 목록 | `window.ENV.STUDENT_PLANNER_COURSES` (JS 평가) |
| 강의 목록 URL | `https://canvas.ssu.ac.kr/courses/{course_id}/external_tools/71` |
| 강의 목록 iframe | `iframe#tool_content` → `#root` (data-course_name, data-professors) |
| 주차/강의 파싱 | `.xnmb-module-list`, `.xnmb-module_item-outer-wrapper` 등 `.xnmb-*` 클래스 |
| 완료 여부 | `[class*='module_item-completed']` (completed / incomplete) |
| 출석 상태 | `[class*='attendance_status']` (attendance / late / absent / excused) |
| 비디오 | `video.vc-vplay-video1` |

## Git 커밋 규칙

형식: `type(scope): 한국어 설명` — 첫 줄 72자 이내

| type | 용도 |
|------|------|
| feat | 새 기능 |
| fix | 버그 수정 |
| refactor | 리팩토링 |
| docs | 문서 |
| test | 테스트 |
| chore | 빌드/도구 설정 |

## 보안 주의사항

아래 항목은 `.gitignore`에 등록되어 있음. 커밋 전 `git status`로 반드시 확인.

- `.secret_key` — 암호화 키. **절대 커밋 금지**
- `db/` — 설정 DB(`app.db`). **절대 커밋 금지** (`db/.gitkeep`만 추적)
- `downloads/` — 다운로드/변환/요약 산출물. **절대 커밋 금지** (`downloads/.gitkeep`만 추적)

**민감 정보 처리**: 학번/비밀번호는 자동 로그인을 방지하기 위해 DB에 저장하지 않고 현재 프로세스 메모리에만 유지한다. API 키와 텔레그램 토큰은 `crypto.py`로 암호화되어 DB에 저장되며 평문으로 저장되지 않는다.
