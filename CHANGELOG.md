# Changelog

버전 형식: `연도.메이저.마이너` (메이저: 새 기능 추가, 마이너: 버그 수정·내부 변경) — v26.7.0부터 적용. 이전에는 `연도.월.버전` 형식이었음.

## [v26.8.7] - 2026-09-01

### Changed

- **자동 모드는 이제 로그아웃·백엔드 재시작에도 유지된다**: 이전엔 명시적 로그아웃 시
  지속 상태(`AUTO_ENABLED`)를 껐지만, 그러면 세션(로그인)을 계속 유지해야만 자동 모드가
  살아있어 취지에 맞지 않았다. 이제 **`자동 모드 중지` 버튼**으로만 꺼지고, 로그아웃·재시작
  후 재로그인 시 자동으로 재개된다. (학번/비밀번호는 메모리 전용이라 로그아웃 상태에서
  루프를 계속 돌릴 수는 없어, 재로그인 시점에 복원한다.)
  - `backend/api/routes/auth.py`: 로그아웃 시 `AUTO_ENABLED`를 건드리지 않음
  - `backend/api/routes/auto.py`: 재생 취소 경로도 지속 상태를 끄지 않음

### Added

- 자동 모드 복원 동작을 로그로 확인할 수 있도록 `backend.api.routes.auto` /
  `backend.api.routes.auth`에 INFO 로그 추가, 앱 로거를 stdout에 연결
  (`backend/main.py` — `LOG_LEVEL` 환경변수로 조정 가능, 기본 INFO)

### Internal

- 로그아웃 후 재로그인 시 자동 모드 재개 회귀 테스트 추가 (156/156 통과)

## [v26.8.6] - 2026-09-01

### Fixed

- **이미 생성된 AI 요약이 학습 결과·강의 목록에 반영되지 않던 문제**: 다운로드 파이프라인
  재편(v26.7.0) 이후 요약본은 `downloads/summarized/{과목}/{주차}/{제목}_summarized.txt`에
  저장되는데, 요약 조회 로직(`backend/api/summary_store.py`)은 이전 경로
  (`downloads/{과목}/{주차}/...`)와 `db/summaries/`만 확인해 실제 파일을 찾지 못했다.
  - `find_summary_path()`에 파이프라인 실제 저장 경로를 후보로 추가 → 강의 상세에서
    "AI 요약" 버튼을 누르지 않아도 "요약 내용 보기"가 바로 표시됨
  - `list_summaries()`가 `downloads/summarized/` 아래 요약본도 스캔 → 학습 결과 목록에
    자동 표시 (같은 강의가 canonical `.md`와 파이프라인 `.txt` 양쪽에 있으면 `.md` 우선)

### Internal

- 파이프라인 생성 요약본 인식 회귀 테스트 3건 추가 (155/155 통과)

## [v26.8.5] - 2026-09-01

### Fixed

- **웹 설정의 요약 프롬프트가 2줄로 잘려 보이던 문제**: AI 섹션이 숨겨진 상태에서 textarea
  자동 높이 계산이 실행돼 `scrollHeight=0`으로 접히던 버그. `rows`를 12로 늘리고, 높이 계산을
  섹션 표시가 확정된 뒤(`requestAnimationFrame`)로 미루도록 수정 (`frontend/js/settings.js`,
  `frontend/index.html`)
- **채플 과목 요약 섹션이 누락되던 문제**: 감지 조건이 `"비전채플"` 완전일치 부분문자열이라
  `채플`, `비전 채플`(공백), `비전채플Ⅱ`, `Chapel` 등 표기 변형에서 `[강연자 소개]`/`[성경 말씀]`
  섹션이 붙지 않았다. 공백 제거·소문자 정규화 후 `채플`/`chapel` 포함 여부로 감지하도록 개선
  (`src/summarizer/summarizer.py` `is_chapel_course`)

### Added

- **추가 요약 지시사항 입력란** (`SUMMARY_PROMPT_EXTRA`): 웹 설정 화면에 없어 웹 전용
  사용자가 설정할 수 없던 항목을 추가. 빈 값으로 저장해 초기화도 가능
- **채플 과목 자동 섹션 on/off** (`CHAPEL_SUMMARY_ENABLED`, 기본 켜짐): 웹 설정에서 채플
  과목의 강연자·성경 말씀 섹션 자동 추가를 끌 수 있도록 토글 추가. 요약 파이프라인
  전 경로(자동 다운로드/수동 요약/파일에서 요약)에 반영
- 요약 프롬프트가 기본값과 동일하면 빈 값으로 저장해, 이후 기본 프롬프트가 개선되면 자동 반영

### Internal

- 채플 감지·프롬프트 구성·설정 왕복 회귀 테스트 12건 추가 (152/152 통과)

## [v26.8.4] - 2026-09-01

### Fixed

- **자동 모드가 로그아웃/재시작 후 풀리던 문제**: 자동 모드 활성 상태와 스케줄이 프로세스
  메모리에만 있어, 컨테이너/백엔드가 재시작되면 세션과 함께 사라졌다. 이제 활성 상태·스케줄을
  DB(`AUTO_ENABLED`, `AUTO_SCHEDULE_HOURS`)에 저장하고, **백엔드 재시작 후 재로그인 시 자동으로
  재개**한다 (`backend/api/routes/auth.py`, `backend/api/routes/auto.py`, `src/config.py`).
  단, 사용자가 "자동 모드 중지"를 누르거나 명시적으로 로그아웃한 경우에는 지속 상태를 꺼서
  재로그인해도 복원하지 않는다.
- **`.secret_key` 동시 최초 생성 시 빈 키를 읽던 레이스 (CI 테스트 간헐 실패)**: 여러 스레드가
  동시에 키를 처음 생성할 때, 파일이 막 생성됐지만 쓰기 전인 순간 다른 스레드가 읽으면 빈
  바이트(`b''`)를 키로 사용할 수 있었다. `FileExistsError` 경로에만 있던 재시도 읽기를 파일이
  이미 존재하는 경로에도 적용해 해소 (`src/crypto.py`)

### Internal

- 자동 모드 지속·복원, 키 생성 레이스 회귀 테스트 6건 추가 (140/140 통과)

## [v26.8.3] - 2026-09-01

### Fixed

- **로그인 실패 (SSO 사이트 선택 페이지 추가)**: 숭실대가 `canvas.ssu.ac.kr` 로그인 앞단에
  "로그인 할 사이트를 선택해 주세요" 페이지(`xn-sso/customs/canvas-discovery/login.php`)를
  새로 추가하면서, 기존 로그인 자동화가 이 단계를 넘지 못해 `input#userid` 대기 중 타임아웃 →
  로그인 실패(401)가 발생하던 문제 수정 (`src/auth/login.py`) — `perform_login()`에서
  사이트 선택 페이지 감지 시 "숭실대학교"(`a.btn-ssu-main`)를 클릭해 실제 로그인 폼으로
  진입하도록 처리. 이후 통합 로그인(`.login_btn a`) → `smartid.ssu.ac.kr` 단계는 기존과 동일

## [v26.8.2] - 2026-08-31

### Fixed

- **`.secret_key` 경고 로그 도배**: Docker 바인드 마운트로 `.secret_key`가 디렉토리로 생성된 환경에서,
  암호화/복호화가 호출될 때마다 "디렉토리입니다" 경고가 찍혀 설정 저장 1회당 4줄씩 로그가 쌓이던 문제 수정
  (`src/crypto.py`) — 경고는 프로세스당 1회만 출력하도록 변경

### Changed

- **Fernet 키 캐싱**: `encrypt`/`decrypt` 마다 `.secret_key` 파일을 디스크에서 다시 읽던 것을
  경로별 `lru_cache`로 프로세스당 1회만 로드하도록 개선 (`src/crypto.py`)
- **요약 API 요청 타임아웃 명시**: Gemini/OpenAI/OpenRouter 클라이언트에 300초 타임아웃을 지정해,
  응답이 지연되는 provider가 요약 작업 스레드를 무한정 점유하지 못하도록 방지 (`src/summarizer/summarizer.py`)

### Internal

- **`data/` 경로 잔재 정리**: 설정 DB·다운로드 경로가 `db/`·`downloads/`로 전환된 뒤에도 남아 있던
  구버전 `data/app.db`와 빈 `data/` 디렉토리 제거, 관련 문서·주석의 경로 표기 정정
  (`src/db.py` docstring, `backend/Dockerfile`의 `mkdir` 경로, `CLAUDE.md` 프로젝트 구조·보안 주의사항)
- `.gitignore`: 앱이 사용하지 않는 stray `download/`(단수) 디렉토리와 로컬 dev 가상환경(`.venv-*/`) 추가,
  더 이상 존재하지 않는 `data/*` 규칙 제거, `db/.gitkeep`·`downloads/.gitkeep` 추적 시작

### Tests

- `.secret_key` 디렉토리 경고 1회 출력, Fernet 인스턴스 경로별 캐시 재사용 회귀 테스트 추가
- 전체 134/134 통과, Ruff lint/format 통과

---

## [v26.8.1] - 2026-08-13

### Changed

- **AI 요약 모델 카탈로그 최신화**: 2026년 8월 공식 모델 기준으로 Gemini와 OpenAI 선택 목록을 갱신
  - Gemini: 종료된 1.5/2.0 계열을 제거하고 3.6 Flash, 3.5 Flash/Flash-Lite, 3.1 Pro 등을 추가
  - OpenAI: GPT-5.6 Sol/Terra/Luna, GPT-5.5, GPT-5.4 계열 등을 추가
  - 기본 모델을 요약 워크로드에 적합한 `gemini-3.5-flash-lite`, `gpt-5.6-luna`, `openrouter/auto`로 변경
- **모델 선택 UI 개선**: 고정 드롭다운을 검색·직접 입력 가능한 모델 ID 필드로 변경해 신규 모델을 즉시 사용 가능
- **OpenRouter 실시간 카탈로그**: 공식 Models API에서 텍스트 모델 전체를 조회하고, 저장된 API 키가 있으면 계정의 provider 선호·개인정보 정책·가드레일을 반영한 `/models/user` 결과를 사용
- **Gemini 3.x 호환성**: 3.x 모델에서 지원하지 않는 `thinking_budget=0` 설정을 제외해 요약 요청 실패 방지

### Fixed

- OpenRouter에서 실제 사용 가능한 모델이 수백 개임에도 설정 화면에 5개만 노출되던 문제 수정
- OpenRouter Models API 장애 시에도 최신 기본 목록과 직접 입력을 사용할 수 있도록 fallback 추가

### Tests

- 최신 모델 ID, OpenRouter 계정 필터 API, 장애 fallback, 웹 카탈로그 route 회귀 테스트 추가
- 전체 132/132 통과, Ruff lint/format 및 프론트엔드 JavaScript 문법 검사 통과

---

## [v26.8.0] - 2026-08-07

### AI 요약 provider 다중 지원 (Gemini/OpenAI/OpenRouter) · 릴리즈 프로세스 정리

#### 추가

- **AI 요약 provider 확장**: Gemini 전용이던 AI 요약이 OpenAI, OpenRouter API도 지원하도록 확장
  - `src/summarizer/summarizer.py`: `_summarize_openai`/`_summarize_openrouter` 추가 (OpenRouter는 `openai` SDK를 `base_url`만 바꿔 재사용), provider별 모델 목록 상수(`OPENAI_MODEL_IDS`, `OPENROUTER_MODEL_IDS` 등) 추가
  - `src/config.py`: provider별 API 키/모델 저장 필드(`OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_MODEL`, `OPENROUTER_MODEL`) 추가, `Config.get_ai_api_key()`/`get_ai_model()`/`has_ai_credentials()`로 provider 라우팅을 일원화 (기존 `GOOGLE_API_KEY`/`GEMINI_MODEL` 필드는 하위 호환 유지)
  - 설정 화면(웹 대시보드): 기존 "Gemini 모델" 선택을 "AI 모델" provider 드롭다운으로 변경, 선택한 provider에 따라 하위 모델 목록·API 키 입력이 전환되도록 UI 개편 (`frontend/index.html`, `frontend/js/settings.js`)
  - `pyproject.toml`: `openai` 패키지 의존성 추가

#### 변경

- GitHub Release 제목 형식을 `study-dashboard vX.Y.Z` → `vX.Y.Z`로 단순화 (`.github/workflows/release.yml`)
- `README.md`: Gemini 전용 문구를 "AI 요약"으로 일반화, `docs/gemini-api-key.md` 삭제 (provider 다중화에 따라 특정 API 키 발급 가이드 제거)

#### 테스트

- `tests/test_summarizer.py`, `tests/test_config.py`에 provider별 요약 함수·라우팅·AI 활성화 게이팅 테스트 추가 (129/129 통과)

---

## [v26.7.4] - 2026-08-07

### CRITICAL: 로그인 후 모든 페이지에서 "로그인이 필요합니다" 오류

v26.7.2 배포 인프라 개편 시 `docker-compose.yml`의 backend 커맨드를 `--workers 4`로 바꾼 것이
원인이었다. 로그인 상태(`app_state.scraper`)는 프로세스 메모리 싱글턴인데(Redis 등 외부 세션
저장소 없음 — 단일 사용자 로컬 세션 전제로 설계됨), uvicorn `--workers 4`는 독립된 OS 프로세스
4개를 fork한다. 로그인 요청을 처리한 워커의 메모리에만 세션이 저장되고, 이후 요청은 다른 워커로
라우팅될 수 있어 그 프로세스에서는 로그인한 적이 없는 것으로 보여 401이 발생했다.

#### 수정

- `docker-compose.yml`: backend 커맨드에서 `--workers 4` 제거, 단일 프로세스로 고정
  (재발 방지를 위해 절대 워커를 늘리면 안 된다는 주석 추가)
- **`.secret_key` 동시 생성 시 레이스로 빈 키를 읽는 문제**: `os.open(O_CREAT|O_EXCL)`로 파일을
  원자적으로 생성해도 뒤이은 `os.write()`는 별도 단계라, 경쟁에서 진 스레드/프로세스가
  `FileExistsError`를 잡고 곧바로 읽으면 아직 쓰기가 끝나지 않은 빈 파일을 읽을 수 있었음
  (CI에서 `test_concurrent_key_creation_is_consistent` 8-스레드 테스트로 재현·발견).
  쓰기가 끝날 때까지 짧게 재시도하도록 수정 (`src/crypto.py`)

#### 테스트

- `test_crypto.py::test_concurrent_key_creation_is_consistent` 10회 연속 통과로 레이스 해소 확인
- 전체 122/122 통과

---

## [v26.7.3] - 2026-08-07

### GitHub Actions Docker Hub 배포 복구 · 버전 체크 저장소 참조 수정

v26.7.2를 실제로 GitHub Actions를 통해 Docker Hub에 배포하는 과정에서 발견된 배포 인프라 결함 수정.

#### 수정

- **`release.yml`이 v26.7.0 리팩터링 이전 상태로 방치되어 태그 배포가 실패하던 문제**: 루트 `Dockerfile`을
  찾던 것을 `backend/Dockerfile` + `frontend/Dockerfile` 개별 빌드·푸시로 교체, GitHub Release 안내문도
  폐기된 `study-helper` TUI 사용법 대신 실제 dashboard 설치 절차로 교체 (참고: `study-helper` 프로젝트의 배포 방식)
- **`docker-compose.yml` 배포 모드 전환**: 기본값을 Docker Hub 이미지 pull 방식으로 변경
  (`igor0670/study-dashboard-backend`, `igor0670/study-dashboard-frontend`). 로컬 소스 빌드는 주석 처리된
  대안으로 유지
- **버전 체크가 엉뚱한 프로젝트를 참조하던 문제**: `src/updater.py`가 여전히 `igor0670/study-helper`(별개 프로젝트)의
  Docker Hub 태그를 조회하고 있어 "업데이트 있음" 배너가 study-dashboard가 아닌 study-helper의 최신 버전으로
  잘못 표시되던 버그 수정 — `igor0670/study-dashboard-backend`를 조회하도록 교체
- CI `ruff format` 실패 수정 (`src/summarizer/summarizer.py` 긴 라인 줄바꿈)

#### 테스트

- `test_updater.py` 신규 추가 (5건)
- 전체 122/122 통과, ruff lint·format 클린

---

## [v26.7.2] - 2026-08-07

### 코드 리뷰 후속 조치 — 설정 배너 오탐, 보안·안정성 MEDIUM 10건, 프론트/백엔드 생명주기 8건, 코드 정리 LOW 7건

2026-08-06 3-agent 전체 코드 리뷰에서 나온 CRITICAL·HIGH 항목은 v26.7.0/v26.7.1에서 처리했고,
이번 릴리즈는 남은 MEDIUM·LOW 항목을 정리한 버그 수정·내부 정리 릴리즈다.

#### 수정 (놓쳤던 HIGH 1건)

- **설정 미비 배너 permanent false positive**: `GET /api/settings`가 보안상 API 키를 응답에서 제외하는데,
  프론트는 그 값 존재 여부로 배너를 띄워 키가 있어도 항상 "미설정" 경고가 뜨던 문제 수정.
  `HAS_GOOGLE_API_KEY`/`HAS_TELEGRAM_BOT_TOKEN` boolean 필드 추가 (`backend/api/routes/settings.py`, `frontend/js/app.js`)

#### 수정 (보안·안정성 MEDIUM 10건)

- **ffmpeg subprocess timeout 없음**: 손상된 mp4에서 무한 대기하던 문제 — 30분 타임아웃 + `TimeoutExpired` 처리 추가
- **다운로드 파이프라인 부분 실패 시 파일 정보 유실**: mp4/mp3까지 받은 뒤 STT/요약에서 실패하면 이미 완료된 파일 정보가
  통째로 버려지던 문제 — `PipelineStageError`로 부분 결과를 보존해 `task_manager`가 살려서 전달하도록 수정
- **Gemini 빈 응답 시 TypeError**: 안전 필터 차단·`MAX_TOKENS` 시 `response.text`가 `None`이라 의미 없는 에러로 노출되던 문제 — 명확한 `RuntimeError`로 교체
- **요약 프롬프트 템플릿 `str.format` KeyError 위험**: 사용자 편집 프롬프트에 `{text}` 외 중괄호가 섞이면 STT까지 끝낸 뒤 요약이 실패하던 문제 — `str.replace`로 교체
- **로거 FileHandler 미종료로 fd 누적**: 재생/다운로드 실패마다 새 `FileHandler`를 열고 안 닫아 장시간 구동 시 fd 고갈 위험 — `close_error_logger()` 추가
- **`Config.load()`가 매번 DB 스키마 재생성**: 연결마다 `_ensure_schema()`를 실행하던 것을 경로별 캐싱으로 최초 1회만 실행하도록 수정
- **DB 경로가 CWD 의존**: 저장소 루트가 아닌 곳에서 로컬 서버를 띄우면 빈 DB가 새로 생기던 문제 — 절대경로로 고정
- **학번 평문 로깅 불일치**: 로그인 성공 경로만 마스킹이 빠져 있던 것을 실패 경로와 동일하게 `mask_user_id()` 적용
- **`esc()`가 따옴표 미이스케이프**: HTML attribute 컨텍스트에서 fragile하던 XSS 방어를 `&quot;`/`&#39;` 이스케이프 추가로 보강
- **의존성 취약점**: `starlette` 1.0.0→1.4.1, `cryptography` 46.0.5→50.0.0 등 scoped 업그레이드 (major 버전이 걸리는
  `fastapi`/`google-genai`/`rich`/`playwright`는 회귀 위험이 커서 이번 범위에서 제외)

#### 수정 (프론트엔드 UX 4건 · 백엔드 생명주기 4건)

- **검색창 타이핑 중 다운로드 진행 폴링 고아화**: 강의 목록 재렌더링 시 진행 중인 다운로드 row/버튼이 detach되던 문제 —
  `state.activeDownloads`로 추적해 재렌더링 후에도 폴링을 다시 붙이도록 수정
- **두 폴링 루프가 재생 메시지 로그 엘리먼트를 두고 경쟁**: 자동 다운로드 진행률 표시를 별도 엘리먼트(`#player-auto-download-log`)로 분리
- **로그아웃 후 폴링이 안 멈추던 문제**: `showLogin()`이 `state.userId`를 비우도록 수정해 WS `onclose`의 재폴링 재시작을 차단
- **요약 폴링 체인이 로그아웃 시 취소 안 되던 문제**: 재귀 `setTimeout`을 `state.downloadTaskTimers`에 등록해 일괄 취소 가능하도록 수정
- **서버 종료 시 실행 중 task 미취소**: `docker compose down` 중 부분 상태로 방치되던 것을 lifespan 종료 시 일괄 취소하도록 수정
- **로그아웃 시 scraper 정리 중 AttributeError**: 아직 실행 중인 task가 있으면 scraper 정리를 다음 로그인으로 미루도록 수정
- **`stop_play`가 취소 완료 전에 재생 가능 상태로 복귀**: Playwright 정리가 끝나기 전에 재생 재시도가 가능하던 문제 — 중복 대입 제거로 실제 정리 완료를 유일한 신뢰 소스로 삼도록 수정
- **다운로드 Config 매핑 3중 중복·드리프트**: `tasks.py`/`player.py`/`auto.py`에 흩어져 있던 Config→kwargs 매핑을 `run_download_from_config()` 헬퍼로 통합

#### 정리 (코드 품질 LOW 7건)

- 요약 파일 root 경로 계산 캐싱, 레거시 요약 경로 헬퍼 삭제, `_require_auth()` 8개 파일 복붙을 공용 dependency로 통합,
  fragile DOM 참조 정리, 과도하게 넓은 예외 처리 축소, 스트리밍 다운로드 응답 미종료로 인한 소켓 누수 수정,
  재생 취소 신호를 문자열 비교 대신 명시적 플래그로 교체

#### 테스트

- 회귀 테스트 20건 추가 (신규 파일 `test_db.py`, `test_logger.py`, `test_main_lifespan.py`)
- 전체 117/117 통과, ruff lint 클린

---

## [v26.7.1] - 2026-08-06

### CI 회귀 테스트 수정 · HIGH 보안·안정성 항목 10건 수정

#### 수정 (CI 회귀 테스트 5건)

- `test_config.py`: `/downloads` 경로 변경에 맞춰 stale 테스트 값 수정 (2건)
- `test_download_pipeline.py`: `convert_to_mp3`/`transcribe`/`summarize`의 `output_path` 파라미터에 맞춰 fake 함수 시그니처 수정 (2건)
- `tasks.py` `start_download`: `DOWNLOAD_ENABLED` 가드 누락 회귀 복구 — 프론트/auto 다운로드는 여전히 이 설정을 체크하는데 수동 다운로드만 검증이 빠져 있었음

#### 수정 (보안·안정성 HIGH 10건)

- **SSRF 방지**: `lecture_url`을 `canvas.ssu.ac.kr`만 허용하는 검증 추가 (`backend/api/validators.py`)
- **요약 저장 경로 분산 수정**: `start_summarize`/`start_summarize_from_file`이 `summarize()`/`transcribe()`에 `output_path`를 명시 전달하도록 수정
- **영상 다운로드가 이벤트 루프를 블로킹하던 문제 수정**: `_stream_download`를 `run_in_executor`로, `time.sleep`을 `asyncio.sleep`으로 전환
- **Whisper 모델 캐시 레이스 컨디션 수정**: `_model_cache` 접근에 락 추가해 동시 STT 호출 시 KeyError·모델 이중 로딩 방지
- **공유 Playwright page 보호 강화**: 동시 다운로드 가드(`tasks.py`), 수동 재생 시 auto 모드 가드(`player.py`), `refresh_courses` 재생 중 가드 + 락 + 실패 시 기존 상태 보존(`courses.py`)
- **`get_data_path()` 경로 불일치 수정**: `/data` → `/db` — 마감 알림 중복전송 방지 상태가 컨테이너 재시작마다 소실되던 문제 해결
- **`.secret_key` 생성 경쟁·복호화 실패 무음 처리 수정**: `O_CREAT|O_EXCL`로 원자적 생성, 복호화 실패(`InvalidToken`) 시 경고 로그 추가
- **`/ws/status` 인증 체크 추가**: 미인증 연결은 `accept()` 전에 거부(close code 1008)

#### 테스트

- 회귀 테스트 22건 추가 (신규 파일 4개: `test_validators.py`, `test_video_downloader.py`, `test_web_courses.py`, `test_web_ws.py`)
- 전체 104/104 통과

---

## [v26.7.0] - 2026-08-06

### 버전 형식 변경 · 다운로드 파이프라인 재편 · 실시간 상태 스트리밍

#### 변경

- **버전 형식 전환**: `연도.월.버전` → `연도.메이저.마이너`
- **다운로드 디렉터리 구조 재편** (`src/downloader/pipeline.py`)
  - `build_download_paths()`가 `(base_dir, mp4)` 2-tuple 대신 `(base_dir, mp4, mp3, txt, summary)` 5-tuple을 반환하도록 변경
  - `video/`, `audio/`, `text/`, `summarized/` 타입별 하위 디렉터리로 저장 위치를 고정 — 기존엔 `mp4.with_suffix()`로 mp3/txt 경로를 추정해 mp3 단독 다운로드 시 경로가 어긋나는 문제가 있었음
  - `transcribe()`, `summarize()`에 `output_path` 파라미터를 추가해 위 구조에 맞춰 결과물을 저장하도록 함
- **다운로드/DB 저장 경로 변경** (`docker-compose.yml`): `./data` → `./db`, `./download` → `./downloads`

#### 추가

- **실시간 상태 스트리밍**: `GET /ws/status` WebSocket 신설 — 재생·자동모드 상태를 2초 주기로 push, 프론트가 폴링 대신 WebSocket으로 전환 (`backend/api/routes/ws.py`, `frontend/js/app.js`)
- **재생 완료 후 자동 다운로드**: `AUTO_DOWNLOAD_AFTER_PLAY` 설정 시 재생 완료를 트리거로 다운로드 task를 자동 생성 (`backend/api/routes/player.py`)
- **미제출 과제/퀴즈 대시보드**: `GET /api/courses/pending-items` 신설, 통계 카드 클릭 시 모달로 목록 표시
- **STT/요약 파일 다운로드**: `GET /api/tasks/{id}/stt/download`, `GET /api/summaries/{id}/download`
- **강의 검색/필터**: 대시보드 강의 목록에 검색창 + 완료여부 필터 추가
- **설정 미비 배너**: AI 요약 등 미설정 시 대시보드 상단 경고 배너 표시
- 사이드바 메뉴명 변경: "요약 대시보드" → "학습 결과"

#### 수정 (버그·인프라)

- **CRITICAL**: `POST /api/tasks/summarize-from-file`이 디렉터리 재편 이후 5-tuple 언패킹 누락으로 항상 `ValueError` → HTTP 400으로 실패하던 문제 수정 (`backend/api/routes/tasks.py`)
- `CLAUDE.md`: 실행 방법이 존재하지 않는 `study-helper` 서비스를 참조하던 것을 실제 `backend`+`frontend` compose 구성으로, 경로 문서(`data/`→`db/`, `/download`→`/downloads`)를 실제와 일치하도록 수정
- CI: `ruff` lint 대상에 `backend/` 추가, `docker-build` job이 존재하지 않는 루트 `Dockerfile`을 빌드하려던 것을 `backend/Dockerfile`+`frontend/Dockerfile` 개별 빌드로 수정, CI/release 트리거를 `workflow_dispatch` → `push`/`pull_request`/태그 자동 트리거로 복구
- `pyproject.toml`: 패키지명/엔트리포인트를 `study-helper` → `study-dashboard`로 변경(대시보드 정체성 반영), `uv.lock` 재생성
- `.gitignore`: `db/`, `downloads/` 누락 항목 추가 — 암호화된 API 키·평문 학번이 담긴 DB 파일이 커밋될 수 있던 위험 제거

#### 테스트

- `tests/test_web_download.py` — `summarize-from-file` 5-tuple 언패킹 회귀 테스트 추가

---

## [v26.06.1] - 2026-06-20

### study-helper 미이식 기능 이식 (1-A~1-D) · 로컬 개발 환경 지원

#### 추가

- **재생 완료 텔레그램 알림** (`backend/api/routes/player.py`)
  - `_notify_playback_complete()` 헬퍼 추가 — 재생이 정상 종료(`final_state.ended`)됐을 때 텔레그램으로 완료 알림 전송
- **재생 미완료 텔레그램 알림** (`backend/api/routes/player.py`)
  - `_notify_playback_error()`에 `failed: bool = True` 파라미터 추가 — 재생 오류는 `failed=True`, 미완료 종료는 `failed=False`로 구분하여 알림 전송
- **자동 모드 5강의마다 브라우저 재시작** (`backend/api/routes/auto.py`)
  - `_run_auto_cycle()` 루프에 `idx % 5 == 0` 조건 추가 — 5강의 재생마다 `scraper.close()` + `scraper.start()` 호출로 Chromium 메모리 누적 방지 (study-helper 동일 패턴)
- **수동 다운로드 요약 완료 텔레그램 전송** (`backend/api/routes/tasks.py`)
  - `_notify_summary_complete()` 헬퍼 추가 — `start_download()` task 완료 후 요약 파일이 존재하면 텔레그램으로 요약 내용 전송
- **로컬 개발 서버 지원** (`backend/main.py`)
  - `frontend/` 디렉토리가 존재할 때 `StaticFiles`로 마운트 — Docker 없이 `uvicorn backend.main:app --port 8000 --reload` 만으로 프론트+백엔드 동시 구동 가능

#### 테스트

- `tests/test_web_player.py` — 재생 완료·오류·미완료 텔레그램 알림 3건 추가 (RED→GREEN)
- `tests/test_web_auto.py` — 자동 모드 5강의 주기 브라우저 재시작 검증 신규 파일
- `tests/test_web_download.py` — 수동 다운로드 요약 완료 텔레그램 전송 검증 추가
- 전체 81/81 통과

---

## [v26.05.1] - 2026-05-20

### study-helper 원본 대비 미마이그레이션·미완성 기능 완성

#### 수정 (안정성·퇴보 복구)

- **`_send_document` 검증 로직 복구** (`src/notifier/telegram_notifier.py`)
  - 파일 존재 여부 확인, 50 MB 크기 제한, HTTP/API 오류 상세 로깅(`_err`) 재추가 — 대시보드 이식 과정에서 누락된 study-helper 원본 로직 복원
- **자동 모드 브라우저 재시작 추가** (`backend/api/routes/auto.py`)
  - `_auto_loop()` 각 사이클 완료 후 `scraper.close()` + `scraper.start()` 호출 — 장시간 자동 재생 시 Chromium 메모리 누적 방지

#### 추가

- **자동 모드 미설정 기능 소프트 경고** (`backend/api/routes/auto.py`, `frontend/index.html`, `frontend/js/app.js`)
  - `POST /api/auto/start` 응답에 `warnings[]` 추가 — 다운로드 자동화·STT·AI 요약·텔레그램 미설정 시 항목별 경고 메시지 포함
  - 프론트엔드 자동 모드 카드에 `#auto-warnings` 배너 추가 — 경고 ON 시 amber 텍스트로 표시, OFF 시 초기화
- **수동 재생 실패 텔레그램 알림** (`backend/api/routes/player.py`)
  - `_notify_playback_error()` 헬퍼 추가 — 재생 오류(`final_state.error`)와 예외(`except Exception`) 두 경로 모두에서 텔레그램 알림 전송
- **과거 학기 요약 탐색 구현** (`frontend/js/app.js`)
  - `loadSummaryTerm()`을 async 함수로 교체 — `GET /api/summaries` 호출 후 선택 학기 필터링, 과목별 그룹 카드 렌더링, 각 요약 클릭 시 요약 팝업 연결
- **버전 체크 API 및 프론트엔드 업데이트 배지** (`backend/main.py`, `frontend/index.html`, `frontend/js/app.js`)
  - `GET /api/version` 엔드포인트 추가 — Docker Hub 최신 태그를 비동기 조회하여 업데이트 가능 여부 반환
  - 로그인 후 버전 조회 자동 실행 — 사이드바 하단에 현재 버전 표시, 업데이트 가능 시 amber 배지 표시

---

## [v26.04.13] - 2026-04-16

### P0/P1 안정성 개선 · 로깅 강화 · 문서 최신화

#### 수정 (버그·안정성)

- **`courses.py` Race condition 제거** (`backend/api/routes/courses.py`)
  - `get_courses()` 엔드포인트에 `_courses_load_lock` 적용 — 동시 요청 시 `fetch_courses()` 중복 실행 방지
  - 강의 목록 로드 실패 시 `503` HTTP 에러로 명확한 응답 반환
- **`crypto.py` 키 경로 로직 통합** (`src/crypto.py`)
  - `_load_or_create_key()`의 인라인 중복 경로 계산을 `_resolve_key_path()` 호출로 통합
  - `.secret_key`가 디렉토리인 경우(Docker 볼륨 마운트) `logger.warning`으로 경고 출력
  - `decrypt()`의 `except (InvalidToken, Exception)` → `InvalidToken`과 일반 `Exception` 분리 처리 — 예상 밖 오류는 `logger.warning` 기록
- **`backend/main.py` 초기화 오류 명확화** (`backend/main.py`)
  - `db.init()` / `Config.load()` 실패 시 `logger.critical` + `RuntimeError` 발생 — 기존 traceback 크래시 → 즉시 원인 파악 가능

#### 개선 (로깅·가시성)

- **`background_player.py` 무음 예외 개선** (`src/player/background_player.py`)
  - networkidle 대기 실패 / Plan A·B commons meta duration 조회 실패 시 `except Exception: pass` → `log(...)` 호출로 교체 (3곳)
- **`video_downloader.py` 프레임 평가 오류 로깅** (`src/downloader/video_downloader.py`)
  - 주석 처리된 디버그 코드 제거, `except Exception: pass` → `logger.debug("frame 평가 오류: %s", e)`로 교체
  - `logging` 모듈 및 `logger` 추가
- **자동 다운로드 완료 파일명 표시** (`frontend/js/app.js`)
  - 재생 후 자동 다운로드 완료 메시지가 파일 타입(`mp4, mp3`)만 표시하던 것을 실제 파일명(`완료: 파일명.mp4`)으로 변경 — 일반 다운로드 완료 표시와 일관성 확보

#### 문서

- **`docs/lms-analysis.md` 최신화** — 3개 섹션 갱신
  - 인증(섹션 2): `expect_navigation` 방식 → 폴링 기반 판정 흐름, JS 다이얼로그 처리, `ensure_logged_in()` 진입점 설명 추가
  - 백그라운드 재생(섹션 5): "ARM64 전용" → "항상 적용"으로 H.264 우회 설명 수정, 사전 처리 라우트/리스너 표(`_sniff_attendance_duration`, `_fix_commons_endat`, `_block_flash_global`) 추가, ErrAlreadyInView 우회 2단계 → 3단계로 갱신
  - 브라우저 설정(섹션 7): 신규 인수 8개 추가(`--disable-web-security`, `--use-fake-ui-for-media-stream`, `--window-size`, `--js-flags`, `--aggressive-cache-discard`, `--renderer-process-limit=2` 등)
- **`docs/web-completeness-checklist.md` 삭제** — 구현 완료로 불필요

---

## [v26.04.12] - 2026-04-15

### P2-A: 텔레그램 테스트, 마감 알림, Task 영속화 + 프론트엔드 모듈 분리

#### 추가
- **텔레그램 연결 테스트** (`backend/api/routes/settings.py`, `frontend/index.html`, `frontend/js/app.js`)
  - `POST /api/settings/telegram/test` 엔드포인트 — 봇 토큰·Chat ID 유효성 검증 후 테스트 메시지 전송
  - 텔레그램 설정 섹션에 "연결 테스트" 버튼 추가 — 성공/실패 결과 인라인 표시
- **마감 임박 알림 웹 연결** (`backend/api/routes/deadline.py`, `backend/main.py`, `backend/api/routes/auto.py`, `frontend/index.html`, `frontend/js/app.js`)
  - `POST /api/deadline/check` 엔드포인트 — 미완료 과제·퀴즈 중 마감 임박 항목 조회, 텔레그램 설정 시 알림 자동 전송
  - 강의 목록 페이지 우상단 "마감 확인" 버튼 — 임박 항목 목록 및 전송 결과 alert 표시
  - 자동 모드 사이클 시작 시 강의 목록 갱신 직후 마감 임박 체크 자동 실행
- **Task SQLite 영속화 + 오래된 Task 정리** (`src/db.py`, `backend/api/task_manager.py`, `backend/main.py`)
  - `tasks` 테이블 스키마 추가 (`src/db.py`) — id, kind, status, stage, result, metadata 등 전체 상태 저장
  - `persist_task()` / `load_tasks()` / `purge_old_tasks()` 헬퍼 함수 추가
  - 완료·실패·취소 task를 DB에 자동 저장, 앱 시작 시 7일치 이력 복원, 만료 task 자동 정리
- **단독 AI 요약 실행** (`backend/api/routes/tasks.py`, `frontend/js/app.js`)
  - `POST /api/tasks/{task_id}/summarize` — STT 완료 task에서 별도 요약 task 생성
  - 다운로드 완료 후 STT가 있으면 "요약 실행" 버튼 동적 추가, 완료 시 "요약 보기"로 전환
- **자동 모드 스케줄 실시간 업데이트** (`backend/api/routes/auto.py`, `frontend/js/app.js`)
  - `PUT /api/auto/schedule` 엔드포인트 — 자동 모드 재시작 없이 스케줄 시간 변경
  - 스케줄 모달 적용 시 실행 중이면 stop+start 대신 PUT 호출

#### 개선
- **프론트엔드 JS 모듈 분리** (`frontend/js/`)
  - `app.js` 60.7KB → 38.9KB (990줄)으로 축소
  - `modals.js` — STT·요약 모달 (93줄)
  - `logs.js` — 로그 조회·렌더링 (172줄)
  - `settings.js` — 설정 폼·`applySettingsVisibility`·`loadAppSettings` (184줄)
  - `summaries.js` — 요약 대시보드 (129줄)
- **README 기술 스택 현행화** (`README.md`)
  - Frontend 뱃지를 React/TypeScript/Vite → HTML5/JavaScript/Tailwind CSS로 교체

---

## [v26.04.11] - 2026-04-15

### 요약 대시보드, STT 웹 통합, 로그인 안정성 개선

#### 추가
- **요약 대시보드** (`frontend/index.html`, `frontend/js/app.js`, `backend/api/routes/summaries.py`, `backend/api/summary_store.py`)
  - 사이드바에 **요약 대시보드** 메뉴 항목 추가
  - `GET /api/summaries` 엔드포인트 — `data/summaries/{term}/{course}/{week}/{title}.md` 구조를 재귀 스캔해 메타데이터 목록 반환
  - `list_summaries()` 헬퍼 함수 (`summary_store.py`)
  - 과목별 필터 칩 + 과목→주차 그룹화 카드 목록 UI
  - 카드 클릭 시 기존 요약 팝업 모달 연결
  - 요약이 없을 때 빈 상태 안내 표시
- **STT 텍스트 조회 API** (`backend/api/routes/tasks.py`)
  - `GET /api/tasks/{task_id}/stt` — 다운로드 태스크의 STT 결과 텍스트 파일 내용 반환
  - 다운로드 완료 시 프론트엔드에서 "STT 보기" 버튼 동적 추가 및 모달 연결
- **Whisper 모델 로딩 단계 세분화** (`src/downloader/pipeline.py`, `src/stt/transcriber.py`)
  - STT 단계를 `stt_loading`(모델 로딩 중) → `transcribing`(변환 중)으로 분리
  - `on_model_loaded` 콜백 추가로 웹 UI에 로딩 상태 실시간 반영

#### 수정
- **로그인 감지 로직 개선** (`src/auth/login.py`, `src/scraper/course_scraper.py`)
  - `"login" in page.url` 단순 URL 체크 → `_needs_login()` 함수로 교체
  - Canvas가 미인증 사용자를 `/login` 없는 URL(예: `/?`)로 리다이렉트하는 경우에도 `.login_btn` 버튼 존재 여부로 정확히 감지
  - `ensure_logged_in()`, `fetch_courses()`, 세션 만료 감지 등 4개 호출부 일괄 적용
- **`player.py` 서버 기동 오류 수정** (`backend/api/routes/player.py`)
  - 존재하지 않는 `get_current_user`를 `src.config`에서 임포트하는 중복 코드 블록 제거
  - `router = APIRouter()` 재선언으로 인한 라우터 덮어쓰기 문제 해결
  - `/status` 엔드포인트 복원

---

## [v26.04.10] - 2026-04-14

### 요약 팝업 모달, P0 보안/안정성 개선 (통계 자동 로딩·CORS·player status)

#### 추가
- **요약 내용 팝업 모달** (`frontend/index.html`, `frontend/js/app.js`)
  - 강의 row의 `요약 내용 보기` 버튼 클릭 시 전체 페이지 이동 대신 오버레이 팝업 표시
  - backdrop blur, X 버튼, backdrop 클릭, ESC 키로 닫기 지원
  - 모달 열리는 동안 body 스크롤 잠금, 닫힐 때 복원
  - `GET /api/summaries/{summary_id}` 호출 → `renderMarkdown()`으로 안전한 마크다운 렌더링
  - 강의명·과목·주차 메타 정보 헤더 표시, 스크롤 가능한 본문 영역
- **통계 오류 배너** (`frontend/index.html`)
  - `#stats-error` 영역 추가 — 통계 로딩 실패 시 에러 메시지 표시

#### 변경
- **`GET /api/courses/stats` 자동 로딩** (`backend/api/routes/courses.py`)
  - `app_state.details`가 비어있을 때 asyncio Lock 획득 후 과목·강의 정보 자동 로딩 (double-check 패턴)
  - 동시 요청 시 중복 로딩 방지
  - LMS 로딩 실패 시 503 + 사용자 친화적 에러 메시지 반환
  - 응답에 `"loaded": true` 필드 추가
  - 프론트의 취약한 `total_videos === 0` 임시 workaround 제거 (백엔드 자동 로딩으로 대체)
- **통계 로딩 spinner/skeleton** (`frontend/js/app.js`)
  - `loadStats()` 호출 즉시 세 통계 카드에 spinner 표시
  - 성공 시 숫자로 교체, 에러 배너 자동 숨김
  - 실패 시 `—` 표시 + `#stats-error` 배너에 에러 메시지 노출
- **CORS 정책 강화** (`backend/main.py`)
  - `allow_origins=["*"]` 제거
  - `CORS_ALLOWED_ORIGINS` 환경변수로 허용 origin 지정 가능 (콤마 구분)
  - 기본값: `http://localhost`, `http://localhost:80`, `http://localhost:443`, `http://127.0.0.1`
  - `allow_credentials=True` 추가
- **`GET /api/player/status` 인증 정책 결정** (`backend/api/routes/player.py`)
  - 로컬 단일 사용자 서비스 + 재생 상태 polling 용도로 인증 불필요 결정
  - 외부 노출 환경에서는 `_require_auth()` 추가 필요함을 주석으로 명시
- **Gemini 모델 설정 드롭다운 전환** (`frontend/index.html`, `frontend/js/app.js`)
  - AI 에이전트 선택 UI 제거 (Gemini 단일 엔진 통합)
  - 모델 텍스트 입력 → 드롭다운 선택으로 변경 (`gemini-2.5-flash` 기본값)
- **요약 프롬프트 textarea 자동 높이** (`frontend/index.html`, `frontend/js/app.js`)
  - 고정 `rows="12"` 제거 → `autoResizeTextarea()` 기반 내용에 맞는 자동 확장

---

## [v26.04.09] - 2026-04-14

### STT 다운로드 파이프라인, AI 요약 파이프라인, 프롬프트 편집 UI, 설정 화면 개선

#### 추가
- **다운로드 → STT 변환 파이프라인 연결** (`src/downloader/pipeline.py`, `backend/api/routes/tasks.py`, `src/config.py`)
  - `download_lecture_media()`에 STT 파라미터 추가: `stt_enabled`, `stt_model`, `stt_language`, `delete_audio_after_stt`
  - 다운로드 완료 후 Whisper STT 변환 step 자동 실행 (mp3 / both 규칙에서만 활성)
  - STT 변환 성공 시 `.txt` 파일을 task result에 포함
  - `delete_audio_after_stt` 옵션 활성화 시 변환 완료 후 mp3 파일 자동 삭제
  - 웹 다운로드 task에서 `Config.STT_ENABLED`, `Config.WHISPER_MODEL`, `Config.STT_LANGUAGE`, `Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE` 반영
  - STT 성공/실패 행위 로그 (`event_type="stt"`) 기록
- **다운로드 → STT → AI 요약 파이프라인 연결** (`src/downloader/pipeline.py`, `backend/api/routes/tasks.py`, `src/summarizer/summarizer.py`)
  - `download_lecture_media()`에 요약 파라미터 추가: `ai_enabled`, `ai_agent`, `ai_api_key`, `ai_model`, `summary_prompt_template`, `summary_prompt_extra`, `delete_text_after_summary`
  - STT 변환 성공 후 Gemini 요약 step 자동 실행
  - 요약 성공 시 `_summarized.txt` 파일을 task result에 포함
  - `delete_text_after_summary` 옵션 활성화 시 요약 완료 후 STT 원본 txt 파일 자동 삭제
  - 웹 다운로드 task에서 `Config.AI_ENABLED`, `Config.AI_AGENT`, `Config.GOOGLE_API_KEY`, `Config.GEMINI_MODEL`, `Config.get_summary_prompt_template()`, `Config.SUMMARY_PROMPT_EXTRA`, `Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE` 반영
  - AI 요약 성공/실패 행위 로그 (`event_type="summary"`) 기록
- **AI 요약 프롬프트 편집 UI** (`frontend/index.html`, `frontend/js/app.js`, `frontend/js/state.js`, `backend/api/routes/settings.py`, `src/config.py`)
  - 웹 설정 화면 AI 요약 섹션에 프롬프트 textarea 추가
  - 편집 버튼으로 readOnly 토글, 편집 완료 후 저장 가능
  - 초기화 버튼으로 `DEFAULT_SUMMARY_PROMPT`로 즉시 복원
  - `SUMMARY_PROMPT_TEMPLATE` 설정으로 저장, `{text}` placeholder에 STT 원문 삽입 지원
  - `GET /api/settings`에 `SUMMARY_PROMPT_TEMPLATE`, `SUMMARY_PROMPT_DEFAULT` 추가
  - `PUT /api/settings`에서 `SUMMARY_PROMPT_TEMPLATE`, `SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE` 처리
- **비전채플 과목 전용 요약 프롬프트 추가** (`src/summarizer/summarizer.py`)
  - 과목명에 "비전채플" 포함 시 `[강연자 소개]`, `[성경 말씀]` 섹션 자동 추가
- **웹 설정 STT 삭제 토글 추가** (`frontend/index.html`, `frontend/js/app.js`)
  - `STT 변환 후 mp3 삭제` 토글: STT 활성 시에만 활성화
  - `AI 요약 후 원본 txt 삭제` 토글: AI 요약 활성 시에만 활성화
  - 다운로드 규칙이 `mp4`이면 STT 섹션 숨김, STT 비활성이면 AI 요약 섹션 숨김
- **로그 조회 메뉴에 AI 요약 필터 추가** (`frontend/index.html`, `frontend/js/app.js`)
  - 사이드바 로그 드롭다운에 `AI 요약` 유형 필터 추가
- **설정 화면 테스트** (`tests/test_web_settings.py`, `tests/test_web_download.py`, `tests/test_config.py`, `tests/test_summarizer.py`, `tests/test_download_pipeline.py`)
  - 다운로드 규칙/STT/AI 설정 저장 및 자동 비활성화 로직 테스트 추가
  - `DOWNLOAD_DIR` DB 무시 정책 테스트 추가
  - 요약 프롬프트 빌드 로직 및 chapel 추가 섹션 테스트 추가
  - 웹 다운로드 task STT/AI 파라미터 전달 테스트 추가

#### 변경
- **Gemini 모델 설정을 텍스트 입력에서 드롭다운 선택으로 변경** (`frontend/index.html`, `frontend/js/app.js`)
  - `gemini-2.5-flash` (무료 티어 지원, 권장), `gemini-2.0-flash`, `gemini-1.5-flash`, `gemini-1.5-pro` 선택 지원
  - DB에 모델 미설정 시 `gemini-2.5-flash` 자동 기본 선택
- **AI 에이전트 선택 UI 제거** (`frontend/index.html`)
  - Gemini 단일 엔진으로 통합됨에 따라 에이전트 선택 드롭다운 제거
- **요약 프롬프트 textarea 자동 높이 조정** (`frontend/index.html`, `frontend/js/app.js`)
  - `rows="12"` 고정 → `autoResizeTextarea()` 기반 내용에 맞는 자동 확장으로 변경
  - 설정 로드·초기화·편집 중 타이핑 시 실시간 높이 반영

---

## [v26.04.08] - 2026-04-14

### 대시보드 재구성, 다운로드 경로 고정, DB 행위 로그 및 로그 조회 UI

#### 추가
- **대시보드 미처리 항목 통계** (`src/scraper/models.py`, `backend/api/routes/courses.py`, `frontend/index.html`, `frontend/js/app.js`)
  - 과제/퀴즈 제출 필요 여부를 집계하는 `needs_submission`, `pending_assignment_count`, `pending_quiz_count` 추가
  - `/api/courses`, `/api/courses/stats`, 강의 상세 주차 payload에 미시청 영상/과제/퀴즈 카운트 추가
  - 메인 대시보드에 `미시청 영상`, `제출 필요 과제`, `제출 필요 퀴즈` 카드 표시
  - 강의 목록 과목 카드에 `미시청 영상 n개 / 과제 n개 / 퀴즈 n개` 형식의 요약 표시
- **DB 기반 행위 로그 저장소** (`src/db.py`, `src/event_log.py`)
  - `event_logs` SQLite 테이블 추가
  - 모든 로그에 `YYYY-MM-DD HH:mm:ss` 형식의 `created_at` 타임스탬프 저장
  - `password`, `token`, `api_key`, `secret`, `cookie`, `authorization` 등 민감 키워드 metadata 자동 마스킹
  - 로그인 실패 시 사용자 ID 일부 마스킹 지원
  - 로그 저장 실패가 본 기능을 막지 않도록 best-effort `record_event()` 구현
- **행위 로그 기록 연결** (`backend/api/routes/auth.py`, `backend/api/routes/settings.py`, `backend/api/routes/player.py`, `backend/api/routes/tasks.py`)
  - 로그인 성공/실패, 로그아웃 기록
  - 설정 변경 성공/실패 및 변경 전후 snapshot 기록 (민감값 마스킹)
  - 영상 재생 시작/완료/실패/중지/중지 요청 기록
  - 다운로드 시작/완료/실패/미지원/취소 요청 기록
- **행위 로그 조회 API/UI** (`backend/api/routes/logs.py`, `backend/main.py`, `frontend/index.html`, `frontend/js/app.js`, `frontend/js/state.js`)
  - `GET /api/logs` 추가: `event_type`, `status`, `limit` 필터 지원
  - 좌측 사이드바에 “로그 조회” 드롭다운 추가
  - `전체 로그`, `로그인/로그아웃`, `설정 변경`, `영상 재생`, `다운로드` 유형별 조회 메뉴 추가
  - 로그 조회 페이지에서 시간, 구분, 상태, 대상, 메시지/오류, 사용자 표시
  - 로그 새로고침 버튼 및 상태별 배지 UI 추가

#### 변경
- **대시보드/강의 목록 표시 정책** (`frontend/index.html`, `frontend/js/app.js`)
  - 메인 대시보드의 기존 `전체 강의 완료 / 전체` 카드 제거
  - 강의 목록 과목 카드의 진행률 텍스트/막대 제거
  - 과목별 미처리 항목 유무에 따라 `진행 필요` / `완료` 상태 표시
- **다운로드 경로 고정** (`src/config.py`, `backend/api/routes/settings.py`, `src/ui/settings.py`, `frontend/index.html`, `frontend/js/state.js`, `docker-compose.yml`)
  - 웹/CLI 설정 화면에서 다운로드 경로 입력 기능 제거
  - `Config.get_download_dir()`가 항상 컨테이너 내부 `/download`를 반환하도록 고정
  - 기존 DB에 저장된 `DOWNLOAD_DIR` 값은 무시
  - `docker-compose.yml`에서 저장소 `./download`를 컨테이너 `/download`로 마운트하도록 명시
  - README의 `DOWNLOAD_HOST_DIR` override 안내 제거 및 `/download` 고정 정책 반영
- **체크리스트 갱신** (`docs/web-completeness-checklist.md`)
  - 대시보드/다운로드 경로 변경 완료 항목 추가
  - DB 행위 로그 및 로그 조회 UI 완료 항목 추가
  - 남은 구현 필요 항목을 최신 상태로 재정리
- **포맷 정리**
  - 전 저장소 `ruff format --check .` 통과를 위해 기존 미포맷 파일 정리

#### 테스트
- **`tests/test_models.py`**
  - 과제/퀴즈 제출 필요 카운트 및 upcoming/completed 제외 검증
- **`tests/test_config.py`**
  - 다운로드 경로 `/download` 고정 및 저장된 과거 `DOWNLOAD_DIR` 무시 검증
- **`tests/test_web_summaries.py`**
  - `/api/courses`, `/api/courses/stats`의 미시청 영상/과제/퀴즈 카운트 payload 검증
- **`tests/test_web_settings.py`**
  - 설정 변경 로그 저장 및 민감값 마스킹 검증
- **`tests/test_event_log.py`**
  - 타임스탬프 형식, 민감 metadata 마스킹, 로그 저장 실패 격리, 사용자 ID 마스킹 검증
- **`tests/test_web_logs.py`**
  - 로그 조회 API 인증 요구 및 event_type/status 필터 검증
- **`tests/test_web_auth.py`**, **`tests/test_web_player.py`**, **`tests/test_web_download.py`**
  - 로그인/로그아웃, 재생, 다운로드 행위 로그 기록 검증

#### 검증
- `uv run ruff format --check .` — 64 files already formatted
- `uv run ruff check .` — All checks passed
- `node --check frontend/js/app.js` — 통과
- `node --check frontend/js/state.js` — 통과
- `uv run pytest` — 62 passed
- `uv run python -m compileall -q backend src tests` — 통과
- `docker compose config` — 통과

## [v26.04.07] - 2026-04-14

### Background Task 공통화 및 프론트 모듈 분리

#### 추가
- **공통 백그라운드 태스크 관리자** (`backend/api/task_manager.py`)
  - `ManagedTask` 상태 모델 추가: `queued` / `running` / `completed` / `failed` / `cancelled`
  - 작업별 `stage`, `message`, `progress_pct`, `result`, `error`, `metadata` 추적 지원
  - `TaskManager.create()`, `cancel()`, `get()`, `list()`로 장시간 작업 실행/취소/조회 흐름 공통화
- **공통 태스크 상태 API** (`backend/api/routes/tasks.py`)
  - `GET /api/tasks` — 등록된 백그라운드 작업 목록 조회
  - `GET /api/tasks/{task_id}` — 단일 작업 상태 조회
  - `POST /api/tasks/{task_id}/cancel` — 작업 취소 요청
- **요약 조회 API** (`backend/api/routes/summaries.py`, `backend/api/summary_store.py`)
  - `GET /api/summaries/{summary_id}` 추가
  - `data/summaries/{term}/{course}/{week}/{lecture}.md` 형식의 신규 요약 저장 위치 조회 지원
  - 기존 다운로드 폴더의 `{lecture}_summarized.txt` 요약 파일도 fallback으로 조회
  - summary id는 파일 경로를 직접 노출하지 않도록 URL-safe base64로 인코딩
- **강의 상세 요약 메타데이터** (`backend/api/routes/courses.py`)
  - 강의별 `has_summary`, `summary_id`, `summary` 필드 추가
  - 완료된 강의에 요약 파일이 있으면 프론트에서 “요약 내용 보기” 버튼을 표시할 수 있도록 연결
- **요약 상세 화면** (`frontend/index.html`, `frontend/js/app.js`)
  - 강의 상세 화면에서 완료+요약 존재 강의에 “요약 내용 보기” 버튼 표시
  - 요약 상세 페이지에서 AI 요약 내용을 마크다운 스타일로 렌더링
  - 마크다운 렌더러는 DOM 생성 + `textContent` 기반으로 동작해 요약 본문 HTML 주입을 방지
- **영상 다운로드 웹 연결** (`src/downloader/pipeline.py`, `backend/api/routes/tasks.py`, `frontend/js/app.js`)
  - `POST /api/tasks/download`로 수동 영상 다운로드 task 시작
  - 설정값에 따라 `mp4` / `mp3` / `both` 저장 지원
  - 완료된 강의 row에 “영상 다운로드” 버튼과 진행 상태 표시 추가
  - 재생 완료 후 자동 다운로드 설정이 켜져 있으면 플레이어 완료 감지 시 다운로드 task 자동 시작

#### 변경
- **웹 재생/자동 모드 태스크 연결** (`backend/api/routes/player.py`, `backend/api/routes/auto.py`)
  - `asyncio.create_task()` 직접 호출을 `task_manager.create()` 기반으로 전환
  - 재생 시작/자동 모드 시작 응답에 `task_id` 반환
  - `/api/player/status`, `/api/auto/status`에서 현재 연결된 task id 노출
  - 로그아웃/중지 시 공통 task manager cancellation 경로 사용
- **프론트 구조 결정: vanilla 유지 + ES module 분리**
  - 기존 단일 `frontend/index.html` inline script를 `frontend/js/` 모듈로 분리
  - `frontend/js/api.js`: API 호출/timeout 처리
  - `frontend/js/utils.js`: DOM selector, escape, time formatting
  - `frontend/js/markdown.js`: 안전한 마크다운 렌더링
  - `frontend/js/state.js`: 전역 앱 상태
  - `frontend/js/app.js`: 페이지 라우팅/이벤트 바인딩/화면 로직
  - `frontend/Dockerfile`, `docker-compose.yml`에 `/js` 정적 파일 배포/개발 마운트 추가
- **강의 상세 UX 개선**
  - 강의 목록 하단 패널 대신 별도 강의 상세 페이지로 전환
  - “강의 목록으로” / “주차별 강의로” 복귀 동선 추가
  - 과목 카드에 키보드 접근성(`role="button"`, `tabindex`) 보강
- **다운로드 설정 UI 및 저장 정책**
  - 설정에 “영상 다운로드” ON/OFF와 “영상 재생 완료 후 자동 다운로드” ON/OFF 추가
  - 영상 다운로드 ON일 때만 확장자 선택(`mp4` / `mp3` / `both`)과 다운로드 경로 입력 활성화
  - 자동 다운로드 OFF일 때 STT/AI 요약 설정 섹션 숨김 및 백엔드 저장 시 `false` 강제
  - Docker 기본 다운로드 경로를 `/download`로 정리하고 호스트 `/download` 마운트 추가
- **`.env` 설정 잔재 제거**
  - `.env.example` 삭제 및 `.env` 마이그레이션 코드 제거
  - README/Gemini/Telegram 문서를 SQLite 설정 DB 기준으로 정리
  - LMS 계정은 DB에서 자동 로드하지 않고 현재 로그인 세션 메모리에만 유지
  - DB에 과거 LMS credential이 남아 있어도 자동 로그인하지 않도록 CLI 자동 로그인 경로 제거
- **개발 잔재 정리**
  - 미사용 `design-sample.html` 제거

#### 테스트
- **`tests/test_task_manager.py`**
  - 공통 task manager 완료/취소 상태 전이 검증
- **`tests/test_web_summaries.py`**
  - 강의 상세 API의 요약 파일 감지 검증
  - 요약 조회 API의 마크다운 읽기 검증
- **`tests/test_web_download.py`**
  - 다운로드 task 생성, 재생 중 다운로드 차단, 다운로드 비활성 설정 차단 검증
- **`tests/test_web_settings.py`**
  - 다운로드/자동 다운로드 토글에 따른 STT/AI 종속 설정 강제 검증
- **`tests/test_config.py`**
  - DB에 LMS credential이 남아 있어도 자동 로그인용으로 로드하지 않는지 검증
  - 설정 reload가 현재 세션 메모리 credential은 유지하는지 검증
- 기존 웹 auth/player 테스트의 app_state reset 범위를 task id/auto task까지 확장

#### 검증
- `node --check frontend/js/*.js` — 통과
- `uv run pytest` — 50 passed
- `uv run ruff check .` — All checks passed
- `docker compose config` — 통과

## [v26.04.06] - 2026-04-14

### OpenAI 제거 — Gemini 단일 요약 엔진으로 통합

#### 변경
- **`src/summarizer/summarizer.py`** — `_summarize_openai()` 함수 및 `elif agent == "openai"` 분기 제거, docstring Gemini 전용으로 수정
- **`src/config.py`** — `OPENAI_API_KEY` 클래스 속성·로드·저장 로직 제거, `save_settings()`의 ai_agent 분기를 Gemini 단일 경로로 단순화
- **`src/ui/auto.py`** / **`src/ui/download.py`** — `Config.OPENAI_API_KEY` 참조 제거, `api_key = Config.GOOGLE_API_KEY` / `model = Config.GEMINI_MODEL` 직접 사용
- **`backend/api/routes/settings.py`** — `_SENSITIVE`에서 `OPENAI_API_KEY` 제거, `SettingsUpdate` 모델에서 `OPENAI_API_KEY` 필드 제거
- **`frontend/index.html`** — AI 에이전트 select에서 OpenAI 옵션 제거, OpenAI API Key 입력 필드 제거
- **`tests/test_summarizer.py`** — `test_summarize_openai_path` 테스트 제거
- **`pyproject.toml`** — `openai>=1.0.0` 의존성 제거
- **`uv.lock`** — openai 패키지 및 관련 의존성 제거 (69 패키지로 축소)

## [v26.04.05] - 2026-04-14

### 강의 목록 학기 선택 UI 추가

#### 추가
- **`GET /api/courses/terms`** (`backend/api/routes/courses.py`)
  - 현재 학기: 로드된 과목 목록에서 최빈 term 자동 감지
  - 과거 학기: `data/summaries/{term}/` 디렉터리 스캔 — 요약 마크다운이 저장된 학기만 반환
  - 현재 과목이 미로드 상태면 `current_term`은 빈 문자열 반환 (클라이언트 폴백)
- **학기 선택 탭 UI** (`frontend/index.html`)
  - 강의 목록 페이지 최상단에 학기 탭 영역 추가 (`#term-selector`)
  - 과거 학기 요약 기록이 없으면 탭 영역 자체를 숨김 — 현재 UX 변화 없음
  - 과거 학기가 있으면 인디고 pill 탭으로 현재/과거 학기 선택 가능
  - 학기 전환 시 강의 상세 패널 자동 닫힘
- **`loadTerms()` / `switchTerm()` / `loadSummaryTerm()`** (`frontend/index.html`)
  - `loadTerms()`: terms API 호출 후 탭 동적 생성
  - `switchTerm(term)`: 탭 활성 상태 갱신, 현재 학기면 `loadCourses()` / 과거면 `loadSummaryTerm()`
  - `loadSummaryTerm(term)`: 요약 기능 구현 전 안내 placeholder 표시 — 추후 요약 API 연결 예정

## [v26.04.04] - 2026-04-14

### 대시보드 UX 개선 및 보안 강화

#### 수정
- **대시보드 통계 초기 로딩 문제** (`frontend/index.html`)
  - 로그인 직후 대시보드 진입 시 통계가 0/0으로 잘못 표시되던 문제 수정
  - `loadStats()` 호출 시 백엔드 `details`가 비어있으면 `GET /api/courses`를 먼저 호출해 채운 뒤 통계를 재조회하도록 개선
  - 이미 과목이 로드된 세션에서는 추가 요청 없이 즉시 통계 갱신 유지
- **로그아웃 버튼 가시성** (`frontend/index.html`)
  - 페이지 내용이 길어질 때 로그아웃 버튼이 화면 밖으로 밀려 보이지 않던 문제 수정
  - `#app-shell`을 `min-h-screen` → `h-screen overflow-hidden`으로, `aside`에 `overflow-y-auto` 추가
  - 사이드바가 뷰포트 내에서 독립 스크롤하므로 어느 페이지에서도 로그아웃 버튼 항상 접근 가능
- **로그아웃 시 로그인 폼 초기화** (`frontend/index.html`)
  - `showLogin()` 진입 시 학번·비밀번호 입력창과 오류 메시지를 초기화
  - 로그아웃 후 잔류 자격증명으로 재로그인 가능하던 문제 수정
  - 세션 만료 등 모든 로그인 화면 전환 경로에 일괄 적용
- **Settings API 인증 추가** (`backend/api/routes/settings.py`)
  - `GET /api/settings`, `PUT /api/settings` 모두 `_require_auth()` 추가
  - 비로그인 상태에서 설정 조회/변경 시 401 반환
- **Auto status API 인증 추가** (`backend/api/routes/auto.py`)
  - `GET /api/auto/status`에 `_require_auth()` 추가 (start/stop은 기존에 이미 인증 적용)
  - `GET /api/player/status`는 로컬 단일 사용자 서비스 특성상 공개 유지
- **프론트 XSS/HTML Injection 방어** (`frontend/index.html`)
  - `esc()` 헬퍼 추가 — `div.textContent` 기반 HTML 특수문자 이스케이프
  - `renderCourseCards()`: `course.name`, `course.term` → `esc()` 적용
  - `loadCourseDetail()`: `week.title` → `esc()`, 강의 row `data-*` 속성은 `element.dataset` 직접 할당
  - `lec.title`, `lec.duration` → `textContent` 직접 할당, `lec.completion` → 화이트리스트 검증
  - 오류 메시지 div의 `err.message` → `esc()` 적용

## [v26.04.03] - 2026-04-03

### 웹 재생 안정성 및 피드백 보강

#### 추가
- **웹 재생 상태 확장** (`backend/api/state.py`)
  - `PlaybackProgress.status`: `idle` / `playing` / `completed` / `error` / `stopped` 상태 구분
  - `PlaybackProgress.log_path`: 웹 재생 실패 시 저장된 진단 로그 경로 노출
- **웹 재생 결과 처리 강화** (`backend/api/routes/player.py`)
  - `play_lecture()` 반환값을 검사해 완료/오류/중지 상태를 명확히 반영
  - 재생 실패 시 `logs/*_web_play.log` 진단 로그 저장
  - 재생 완료 시 캐시된 강의 항목의 `completion`을 즉시 `completed`로 갱신
- **대시보드 재생 피드백 UI** (`frontend/index.html`)
  - 재생 완료/중지/실패 메시지 표시
  - 실패 시 `/api/player/status`의 `error`와 `log_path` 표시
  - 재생 완료 감지 후 통계와 강의 목록 캐시 자동 갱신
- **웹 player route 회귀 테스트** (`tests/test_web_player.py`)
  - 재생 완료 후 강의 completion 갱신 검증
  - 재생 오류가 status/error/log_path에 유지되는지 검증
- **로그인 실패/지연 처리 보강** (`backend/api/routes/auth.py`, `frontend/index.html`)
  - 백엔드 로그인 시도에 45초 제한을 적용해 Playwright 로그인 대기가 무한히 이어지지 않도록 함
  - cancellation에 즉시 응답하지 않는 Playwright 작업도 timeout 시 사용자 응답을 막지 않도록 처리
  - 잘못된 계정/비밀번호 입력 시 SSO 로그인 폼 잔류, alert, 오류 문구를 짧은 폴링으로 감지해 실패 메시지를 더 빠르게 반환 (`src/auth/login.py`)
  - 로그인 성공/실패 판정 단위 테스트 추가 (`tests/test_login.py`) — 폼 잔류 시 빠른 실패, URL 전환 시 성공 시나리오 검증
  - 프론트 로그인 요청에 60초 timeout을 적용하고 실패/timeout 메시지를 로그인 카드에 표시
  - 학번/비밀번호 미입력 시 즉시 경고 메시지 표시
- **로컬 HTTPS 지원** (`frontend/nginx.conf`, `docker-compose.yml`)
  - nginx가 443/TLS를 직접 처리하고 `http://localhost:3000`을 `https://localhost:3443`으로 리다이렉트
  - backend 포트 `8000`은 로컬 호스트에만 바인딩해 브라우저 트래픽은 nginx HTTPS 프록시를 거치도록 조정
  - 최소 보안 헤더(HSTS, nosniff, SAMEORIGIN, Referrer-Policy) 추가
  - stale inline JS 캐시 방지를 위해 정적 응답에 `Cache-Control: no-store` 적용
- **로컬 인증서 생성 도구/문서**
  - `scripts/generate-local-cert.sh`: self-signed localhost 인증서 생성
  - `docs/https-local.md`: HTTPS 실행 및 인증서 신뢰 안내
  - `certs/.gitkeep`: 인증서 디렉터리만 추적하고 실제 인증서/키는 gitignore로 제외

#### 변경
- `POST /api/player/stop`에 로그인 상태 검사를 추가해 비인증 중지 요청을 차단
- backend/test 코드의 Ruff 지적 사항 정리
  - import 정렬
  - `HTTPException` 재발생 원인 명시 (`from None` / `from e`)
  - `Optional[...]` 타입 표기를 `... | None`으로 변경
  - 미사용 import 제거
- `docs/web-completeness-checklist.md`의 완료 항목을 체크 및 취소선으로 표시
- 로컬 HTTPS 인증서 실파일(`certs/local.crt`, `certs/local.key`)을 git 대상에서 제외

#### 검증
- `uv run pytest` — 41 passed
- `uv run ruff check .` — All checks passed
- FastAPI smoke 확인
  - `GET /api/health` → 200
  - `GET /api/auth/status` → 200
  - `GET /api/player/status` → 200
  - 비로그인 `POST /api/player/stop` → 401
  - nginx 설정 검증(`nginx -t`) → successful
  - `docker compose build frontend` → successful

#### 남은 확인
- 실제 LMS 계정으로 재생 성공/실패/중지 케이스 수동 검증 필요
- 브라우저에서 self-signed 인증서 경고 수락 또는 trust store 등록 필요
- 재생 완료 후 LMS 서버 출석 반영까지 실제 확인 필요

---

## [v26.04.02] - 2026-04-02

### 웹 대시보드 (Docker 풀스택 구성)

#### 추가
- **FastAPI 백엔드** (`backend/`)
  - `backend/main.py`: FastAPI 앱 진입점, 앱 시작 시 DB 초기화 및 `Config.load()` 호출
  - `backend/api/state.py`: 전역 앱 상태 (Playwright 세션, 재생 상태)
  - `backend/api/routes/auth.py`: `POST /api/auth/login|logout`, `GET /api/auth/status`
  - `backend/api/routes/courses.py`: `GET /api/courses`, `/api/courses/{id}`, `/api/courses/stats`, `POST /api/courses/refresh`
  - `backend/api/routes/player.py`: `POST /api/player/play|stop`, `GET /api/player/status`
  - `backend/api/routes/settings.py`: `GET|PUT /api/settings`
- **nginx 프론트엔드** (`frontend/`)
  - `frontend/index.html`: SPA 웹 대시보드 (로그인 / 대시보드 / 강의목록 / 설정)
  - `frontend/nginx.conf`: 정적 파일 서빙 + `/api/*` → 백엔드 프록시
  - `frontend/Dockerfile`: nginx:alpine 기반
- **Docker Compose 풀스택 구성** (`docker-compose.yml`)
  - `backend`: FastAPI + Playwright, 소스 볼륨 마운트 + `--reload` 핫 리로드
  - `frontend`: nginx, HTML/nginx.conf 볼륨 마운트로 재빌드 없이 즉시 반영
- **의존성 추가** (`pyproject.toml`): `fastapi>=0.115.0`, `uvicorn[standard]>=0.32.0`
- `uv.lock` 재생성

#### 변경
- `backend/Dockerfile`: ENTRYPOINT/CMD 분리 — `docker-compose.yml`의 `command:`로 오버라이드 가능
- 루트 `Dockerfile` (TUI 전용) 제거 — 웹 대시보드로 대체

---

## [v26.04.01] - 2026-04-01

### 초기 구성

study-helper(HelloJamong/study-helper) 프로젝트에서 마이그레이션.

### 추가
- **SQLite 기반 설정 저장소** (`src/db.py`)
  - 기존 `.env` 파일 방식에서 `data/app.db`로 전환
  - `get / set / set_many` key-value API
  - `migrate_from_env()`: 기존 `.env` 보유 시 최초 실행 시 자동 마이그레이션
- **`Config.load()`** (`src/config.py`)
  - 클래스 속성을 앱 시작 시 DB에서 일괄 로드하는 명시적 초기화 메서드 추가
  - `python-dotenv` 의존성 제거 (`pyproject.toml`)
- **텔레그램 알림** (`src/notifier/`)
  - 재생 완료·실패 알림 (`telegram_notifier.py`)
  - 마감 임박 항목 감지 및 알림 (`deadline_checker.py`)
- **자동 모드** (`src/ui/auto.py`): 미완료 강의 일괄 재생
- **버전 체크** (`src/updater.py`): 과목 로딩과 병렬로 GitHub 최신 버전 확인
- **STT 엔진**: faster-whisper (CTranslate2 기반, torch 불필요)
- **GitHub Actions 워크플로우** (CI, Docker 릴리즈) — 재건 중 자동 실행 비활성화
