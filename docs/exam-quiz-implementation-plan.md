# 예상 문제 생성 기능 — 구현 계획

> **상태: 구현 완료 (v26.10.0, 2026-09-07).** 아래는 설계 기록이며 실제 구현과 대부분 일치한다.
> 파일 업로드 스토리지는 DB 대신 `downloads/materials/` 파일 기반 + base64 경로 ID로 구현했다
> (요약 저장소와 동일 방식). 세부는 `CHANGELOG.md` 참고.

`docs/exam-quiz-feature-review.md`의 실현 가능성 검토를 바탕으로 한 실제 구현 계획.

## 확정된 스코프

| 항목 | 결정 |
|---|---|
| 1차 범위 | **풀 스코프** — 요약본+STT 기반 + PDF/PPTX/DOCX 업로드 자료까지 포함 |
| 저장소 | **SQLite 3개 테이블** (`quiz_sets`, `quiz_questions`, `quiz_attempts`) — `src/db.py` 스키마에 추가 |
| 주관식 채점 | **생성 시 채점기준(정답/모범답안/채점 포인트) 저장 → 채점 시 사용자 답안+기준을 LLM에 1회 호출** |
| 객관식 채점 | 서버 로컬 즉시 채점, 해설은 생성 시점에 저장한 것을 그대로 표시 (AI 호출 없음) |
| 전제조건 | 기존 `Config.AI_ENABLED` / `AI_AGENT` / 암호화 API 키 재사용. 미설정 시 기능 버튼 비활성화 |
| OCR | 이번 스코프 제외 (이미지 슬라이드는 텍스트 추출 실패 시 경고만) |

## 아키텍처 개요

```
[프론트] 시험범위 선택 + 자료 업로드 + 유형/개수 설정
   │  POST /api/quiz/materials   (파일 업로드, 멀티파트)
   │  POST /api/quiz             (문제셋 생성 → 백그라운드 Task)
   ▼
[backend/api/routes/quiz.py]
   │  - 주차별 요약본 조회   → summary_store (기존)
   │  - 주차별 STT 원본 조회 → src/downloader/pipeline.build_download_paths (기존 규칙 재사용)
   │  - 업로드 자료 텍스트   → src/quiz/materials.py (신규: pdf/pptx/docx 추출)
   │  - task_manager.create("quiz_generate", ...)
   ▼
[src/quiz/generator.py]  ← summarizer.py 패턴 복제 (gemini/openai/openrouter 분기)
   │  프롬프트에 JSON 스키마 강제 + 파싱 실패 시 1회 재시도
   ▼
SQLite: quiz_sets / quiz_questions   (문제/보기/정답/해설/채점기준)

--- 풀이 & 채점 ---
[프론트] 풀이 화면 → POST /api/quiz/{set_id}/attempts  (사용자 답안)
   ▼
[backend]  객관식: 로컬 채점
           단답/서술: task_manager.create("quiz_grade", ...) → src/quiz/grader.py (LLM 1회)
   ▼
SQLite: quiz_attempts   (답안/점수/오답사유)
```

## 신규 파일 / 변경 파일

### 백엔드 — 도메인 로직 (`src/quiz/`)

| 파일 | 역할 |
|---|---|
| `src/quiz/__init__.py` | 패키지 |
| `src/quiz/models.py` | `QuizQuestion`, `QuizSet`, `GradeResult` dataclass + 유형 Enum (`multiple_choice` / `short_answer` / `essay`) |
| `src/quiz/materials.py` | 업로드 파일(pdf/pptx/docx) → plain text 추출. 확장자 분기, 추출 실패/빈 텍스트 시 명시적 예외 |
| `src/quiz/source_collector.py` | 과목+주차 리스트 → 요약본/STT/업로드 자료 텍스트를 모아 하나의 컨텍스트 문자열로 병합 (주차·출처 라벨 포함, 길이 상한/트렁케이션) |
| `src/quiz/generator.py` | `summarizer.py`의 provider 분기(`_summarize_gemini` 등)를 복제. `generate_quiz(context, counts, agent, api_key, model) -> list[QuizQuestion]`. JSON 스키마 프롬프트 + `json.loads` 실패 시 1회 재시도, 2회 실패 시 `QuizGenerationError` |
| `src/quiz/grader.py` | `grade_subjective(questions_with_answers, agent, api_key, model) -> list[GradeResult]`. 문제+채점기준+사용자답안을 배치로 LLM에 전달, JSON 결과(점수 0~100 또는 정답여부 + 오답사유) |
| `src/quiz/store.py` | SQLite CRUD — `create_quiz_set`, `add_questions`, `get_quiz_set`, `list_quiz_sets`, `save_attempt`, `get_attempt`, `list_attempts`. (요약 저장소가 파일 기반이라 별도 store 모듈로 분리) |

### 백엔드 — API (`backend/api/`)

| 파일 | 변경 |
|---|---|
| `backend/api/routes/quiz.py` | **신규** 라우터. 아래 엔드포인트 |
| `backend/main.py` | `quiz` 라우터 `include_router(prefix="/api/quiz")` 등록 |
| `src/db.py` | `_ensure_schema()`에 3개 `CREATE TABLE IF NOT EXISTS` + 인덱스 추가. `quiz_store`용 헬퍼 커넥션은 기존 `_connect()` 재사용 |
| `backend/api/task_manager.py` | 변경 없음 (kind만 `"quiz_generate"` / `"quiz_grade"` 신규 사용) |
| `backend/api/validators.py` | 업로드 파일명/확장자/크기 검증 헬퍼 추가 |

#### 엔드포인트 (`/api/quiz`)

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/materials` | 멀티파트 파일 업로드 → `/downloads/materials/{course}/{week}/{filename}` 저장, 텍스트 추출 미리보기(글자수) 반환 |
| `GET` | `/materials?course_id=&weeks=` | 업로드된 자료 목록 |
| `DELETE` | `/materials/{material_id}` | 업로드 자료 삭제 |
| `POST` | `` | 문제셋 생성 시작. body: `course_id`, `weeks[]`, `material_ids[]`, `counts{multiple_choice,short_answer,essay}` (각 0~20), `include_stt`(bool). → `{task_id}` |
| `GET` | `` | 문제셋 목록 (과목/주차범위/생성일/문항수) |
| `GET` | `/{set_id}` | 문제셋 상세 — 풀이용(정답/해설/기준 제외) |
| `DELETE` | `/{set_id}` | 문제셋 삭제 |
| `POST` | `/{set_id}/attempts` | 답안 제출. body: `answers[{question_id, answer}]`. 객관식 즉시 채점 + 주관식 있으면 `{task_id}` 반환 |
| `GET` | `/{set_id}/attempts/{attempt_id}` | 채점 결과 (점수/문항별 정오/오답사유/해설) |
| `GET` | `/{set_id}/attempts` | 해당 문제셋 풀이 이력 |

모든 엔드포인트: `require_auth()` + `Config.AI_ENABLED == "true"` 및 키/모델 존재 체크 (생성·주관식 채점 한정). `event_log.record_event(event_type="quiz", ...)`로 생성/채점 성공·실패 기록.

### 프론트엔드 (`frontend/`)

| 파일 | 변경 |
|---|---|
| `frontend/index.html` | 사이드바 `nav-item data-page="quiz"` 추가. `#page-quiz`(문제셋 목록+생성 폼), `#page-quiz-solve`(풀이), `#page-quiz-result`(채점 결과) 섹션 추가. 생성 폼: 과목 select, 주차 체크박스 목록, 자료 업로드 dropzone, 유형별 개수 stepper(0~20), STT 포함 토글 |
| `frontend/js/quiz.js` | **신규** — 목록 로드/렌더, 생성 폼 제출, 업로드, task 폴링, 풀이 화면 렌더(문항별 입력), 답안 제출, 결과 렌더. `tasks` 폴링은 기존 `app.js`의 다운로드 task 폴링 패턴 재사용 |
| `frontend/js/app.js` | `navigate()`에 `if (page === 'quiz') loadQuizSets();` 분기. import 추가 |
| `frontend/js/settings.js` | `applySettingsVisibility()` — AI 미설정 시 퀴즈 메뉴/버튼 비활성 안내 (기존 요약 기능과 동일 패턴) |
| `frontend/js/markdown.js` | 재사용 (서술형 해설 렌더) |

### 의존성 (`pyproject.toml` + `uv.lock`)

```toml
# 강의자료 텍스트 추출
"pypdf>=5.0.0",
"python-pptx>=1.0.0",
"python-docx>=1.1.0",
# FastAPI 멀티파트 업로드
"python-multipart>=0.0.9",
```

- 추가 후 `uv lock` → `docker compose build` (CLAUDE.md 규칙)
- `python-pptx`는 `lxml`, `Pillow`를 끌어옴 — 이미지 처리용이며 Dockerfile 시스템 패키지 추가는 불필요(휠 제공). 빌드 후 이미지 크기 증가 확인.
- torch 규칙과 무관 (STT 엔진 그대로).

## SQLite 스키마

```sql
CREATE TABLE IF NOT EXISTS quiz_sets (
    id            TEXT PRIMARY KEY,          -- uuid hex
    course_id     TEXT NOT NULL,
    course_name   TEXT NOT NULL,
    term          TEXT NOT NULL DEFAULT '',
    weeks_json    TEXT NOT NULL DEFAULT '[]',-- 선택된 주차 라벨 목록
    counts_json   TEXT NOT NULL DEFAULT '{}',-- {multiple_choice, short_answer, essay}
    source_json   TEXT NOT NULL DEFAULT '{}',-- {include_stt, material_ids, summary_count, ...}
    agent         TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'ready',  -- ready / failed
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quiz_sets_created_at ON quiz_sets(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_quiz_sets_course ON quiz_sets(course_id, created_at DESC);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id            TEXT PRIMARY KEY,
    set_id        TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,          -- 문제셋 내 표시 순서
    qtype         TEXT NOT NULL,             -- multiple_choice / short_answer / essay
    question      TEXT NOT NULL,
    choices_json  TEXT NOT NULL DEFAULT '[]',-- 객관식 5개 보기
    answer        TEXT NOT NULL DEFAULT '',  -- 객관식: 정답 인덱스, 단답: 모범답안
    rubric        TEXT NOT NULL DEFAULT '',  -- 주관식 채점 기준/포인트
    explanation   TEXT NOT NULL DEFAULT '',  -- 해설 (생성 시 저장)
    FOREIGN KEY (set_id) REFERENCES quiz_sets(id)
);
CREATE INDEX IF NOT EXISTS idx_quiz_questions_set ON quiz_questions(set_id, ordinal);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id            TEXT PRIMARY KEY,
    set_id        TEXT NOT NULL,
    answers_json  TEXT NOT NULL DEFAULT '[]',-- [{question_id, answer}]
    results_json  TEXT NOT NULL DEFAULT '[]',-- [{question_id, correct, score, feedback}]
    score_total   REAL,                      -- 0~100 환산 총점
    graded_status TEXT NOT NULL DEFAULT 'partial', -- partial(객관식만) / completed / failed
    created_at    TEXT NOT NULL,
    graded_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_set ON quiz_attempts(set_id, created_at DESC);
```

- 삭제 시 CASCADE는 SQLite 기본 비활성 → `store.delete_quiz_set()`에서 questions/attempts 함께 삭제.
- `task_manager`의 7일 purge와 별개로 quiz 데이터는 사용자가 명시 삭제할 때까지 유지.

## LLM 프롬프트 설계

### 문제 생성 (`generator.py`)

- 시스템 지시: "다음 강의 자료를 바탕으로 시험 예상 문제를 출제하는 조교. 자료에 근거해서만 출제."
- 입력: 병합 컨텍스트(주차·출처 라벨 포함) + 유형별 요청 개수.
- **출력 JSON 스키마 강제**:
  ```json
  {
    "questions": [
      {"type":"multiple_choice","question":"...","choices":["","","","",""],
       "answer_index":0,"explanation":"..."},
      {"type":"short_answer","question":"...","answer":"모범답안","rubric":"핵심 키워드/채점 포인트","explanation":"..."},
      {"type":"essay","question":"...","rubric":"평가 기준 3~4개 항목","explanation":"모범 답안 개요"}
    ]
  }
  ```
- 파싱: 코드펜스 제거 → `json.loads`. 실패 시 "JSON만 출력" 재강조하여 1회 재시도. 개수 부족 시 있는 만큼 저장하고 `source_json`에 경고 기록.
- 토큰: 유형별 최대 20 × 3 = 60문항. 컨텍스트가 크면 유형별로 호출 분리(3회)하는 옵션을 `generator.py` 내부 임계값으로 처리 (입력 문자 수 기준).

### 주관식 채점 (`grader.py`)

- 입력: `[{question, rubric, model_answer, user_answer}]` 배열 (단답+서술 모두).
- 출력 JSON: `[{"question_index":0,"score":0-100,"correct":bool,"feedback":"오답/감점 사유와 보완점"}]`
- 서술형 결과에는 "참고용 채점" 안내 문구를 프론트에서 고정 표시.
- 1 attempt = 1 호출 (주관식 문항 전체 배치).

## 작업 순서 (PR 단위)

1. **스키마 + store** — `src/db.py` 3테이블, `src/quiz/store.py`, `src/quiz/models.py` + `tests/test_quiz_store.py`
2. **자료 수집/추출** — `src/quiz/materials.py`, `src/quiz/source_collector.py` + 의존성 추가 + 단위 테스트 (샘플 pdf/pptx/docx fixture)
3. **생성기** — `src/quiz/generator.py` (provider 분기는 summarizer 테스트 패턴 재사용, `requests`/SDK 모킹) + `tests/test_quiz_generator.py`
4. **채점기** — `src/quiz/grader.py` + 테스트
5. **API 라우트** — `backend/api/routes/quiz.py` + `main.py` 등록 + `tests/test_web_quiz.py` (기존 `test_web_download.py`의 FakeScraper/app_state 패턴)
6. **프론트 — 생성 흐름** — 메뉴/페이지/폼, 업로드, task 폴링, 문제셋 목록
7. **프론트 — 풀이/채점 흐름** — 풀이 화면, 답안 제출, 결과 렌더, AI 미설정 게이팅
8. **문서화 + CHANGELOG** — `docs/` 갱신, 설정 항목 표 갱신(신규 설정은 없음), `README`/`CLAUDE.md` 프로젝트 구조에 `src/quiz/` 추가

각 PR: `ruff check` + `pytest` 통과, 커밋 규칙 `feat(quiz): ...`.

## 미결 사항 / 리스크

- **업로드 자료 정리 정책**: 문제셋 삭제 시 원본 파일도 삭제할지, `/downloads/materials`에 계속 둘지. (기본안: 문제셋과 무관하게 자료는 유지, 별도 삭제 API)
- **멀티 워커**: 프로덕션 `--workers 4` 시 `task_manager`는 워커별 인메모리 → 다른 워커가 만든 quiz task 상태를 못 볼 수 있음. 현재 다운로드 기능도 동일 한계이며 단일 사용자 전제라 수용. (문제셋/attempt 자체는 DB라 조회는 정상)
- **이미지 슬라이드**: `python-pptx`는 이미지 안 텍스트를 못 뽑음 → 추출 글자수 0이면 프론트에서 "이 자료는 텍스트가 거의 없습니다" 경고.
- **파일 크기 상한**: 업로드 1건 20MB, 총 컨텍스트 문자 수 상한(예: 12만자) 설정 필요.
- **프롬프트 토큰 초과**: 주차를 많이 선택하면 입력이 큼 → `source_collector`에서 주차별 균등 트렁케이션.
