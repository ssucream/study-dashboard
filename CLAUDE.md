# study-helper: Learning X 백그라운드 학습 도구

숭실대학교 Canvas Learning X(canvas.ssu.ac.kr)의 강의 영상을 Docker 컨테이너 기반 CUI 환경에서
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

- 웹 대시보드(backend + frontend 2서비스) 구조이므로 TUI 시절의 `run --rm` 대신 `up` 사용
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
- **GUI 의존성 추가 금지**: flet, PyQt5 등 GUI 라이브러리 사용 금지. CUI 전용.
- **비디오 셀렉터**: `video.vc-vplay-video1`로 영상 URL 추출. 변경 시 Learning X 쪽 변경 확인 필요.
- **SQLite 설정 저장소만 사용**: `.env` 기반 설정은 제거됨. 신규 설정 항목은 DB와 웹/CLI 설정 화면에만 추가할 것.

## 설계 의도

- **STT 엔진**: faster-whisper (CTranslate2 기반, torch 불필요). base 모델이 기본값.
- **요약 엔진**: Gemini / OpenAI / OpenRouter API 중 선택 (provider: `AI_AGENT`). 키는 DB에서 암호화 로드.
- **설정 저장소**: SQLite (`db/app.db`) — `crypto.py`로 민감값 암호화 후 저장.
- **다운로드 경로**: 컨테이너 내 `/downloads` — 기본 compose에서 호스트 `./downloads`를 마운트.
- **출력 파일**: mp4(영상), mp3(음성, ffmpeg 변환), txt(STT 결과), `_summarized.txt`(요약).
- **백그라운드 재생**: video DOM 폴링(Plan A) + 진도 API 직접 호출(Plan B). Plan A 실패 시 자동 전환.
- **자동 모드**: `ui/auto.py` — 미완료 강의 일괄 재생.
- **마감 알림**: `notifier/deadline_checker.py` — 로그인 직후 미제출 과제/마감 임박 항목 텔레그램 알림.
- **버전 체크**: `updater.py` — 과목 목록 로딩과 병렬로 GitHub 최신 버전 확인.

## 프로젝트 구조

```
study-helper/
├── Dockerfile
├── docker-compose.yml
├── src/
│   ├── main.py                           # 진입점, 로그인→설정→과목 선택 루프
│   ├── config.py                         # 설정 로드/저장 (DB 기반으로 전환 중)
│   ├── crypto.py                         # 계정/API 키 암호화·복호화
│   ├── logger.py                         # 로깅 설정
│   ├── updater.py                        # GitHub 최신 버전 확인
│   ├── auth/
│   │   └── login.py                      # Playwright 로그인 처리
│   ├── scraper/
│   │   ├── course_scraper.py             # 과목/주차/강의 목록 스크래핑
│   │   └── models.py                     # Course, LectureItem, Week 등 데이터 모델
│   ├── player/
│   │   └── background_player.py          # 백그라운드 재생 (출석용)
│   ├── downloader/
│   │   └── video_downloader.py           # 영상 URL 추출 + HTTP 스트리밍 다운로드
│   ├── converter/
│   │   └── audio_converter.py            # mp4 → mp3 (ffmpeg)
│   ├── stt/
│   │   └── transcriber.py               # faster-whisper STT
│   ├── summarizer/
│   │   └── summarizer.py                # Gemini/OpenAI API 요약
│   ├── notifier/
│   │   ├── deadline_checker.py           # 마감 임박 항목 감지
│   │   └── telegram_notifier.py          # 텔레그램 알림 전송
│   └── ui/
│       ├── auto.py                       # 자동 모드 (미완료 강의 일괄 재생)
│       ├── login.py                      # 로그인 화면
│       ├── courses.py                    # 과목/강의 선택 화면
│       ├── player.py                     # 재생 진행 화면
│       ├── download.py                   # 다운로드 진행 화면
│       └── settings.py                   # 초기 설정 화면
├── db/
│   └── app.db                           # 설정 DB (호스트 ./db → 컨테이너 /db 볼륨 마운트)
└── downloads/                            # 다운로드 파일 (호스트 ./downloads → 컨테이너 /downloads 볼륨 마운트)
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
| `SUMMARY_PROMPT_EXTRA` | 요약 프롬프트 추가 지시 | — |
| `TELEGRAM_ENABLED` | 텔레그램 알림 사용 여부 | `true` / `false` |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 (암호화) | — |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | — |
| `TELEGRAM_AUTO_DELETE` | 전송 후 파일 자동 삭제 | `true` / `false` |
| `AUTO_ENABLED` | 자동 모드 지속 상태 (앱이 관리, 백엔드 재시작 후 재로그인 시 복원용) | `true` / `false` |
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
