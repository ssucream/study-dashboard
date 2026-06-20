# Spec: study-dashboard 미이식·미구현 기능 목록

> 기준일: 2026-06-20  
> 비교 대상: study-helper (TUI, HelloJamong/study-helper) ↔ study-dashboard (Web, 현재 프로젝트)

---

## 1. study-helper에는 구현되어 있으나 study-dashboard에 미이식/미완성인 기능

### 1-A. 수동 재생 완료 후 텔레그램 완료 알림 없음

| | study-helper | study-dashboard |
|---|---|---|
| 위치 | `src/main.py:125` `_tg_notify_playback_complete()` | `backend/api/routes/player.py` |
| 동작 | 재생 완료 시 텔레그램으로 "시청 완료" 알림 전송 | **재생 실패 알림만 구현됨.** 완료 알림 없음 |

- 자동 모드에서는 `_run_post_play_pipeline()`이 완료 알림을 처리하므로 ✅
- **미구현 범위**: `POST /api/player/play` 재생 완료(`final_state.ended == True`) 분기에서 `notify_playback_complete()` 호출 없음

---

### 1-B. 재생 실패 vs 미완료 텔레그램 알림 미구분

| | study-helper | study-dashboard |
|---|---|---|
| 위치 | `src/notifier/telegram_notifier.py:notify_playback_error` | `backend/api/routes/player.py:_notify_playback_error` |
| 동작 | `failed=True` → "실패" / `failed=False` → "미완료" 메시지 구분 | `failed` 파라미터 없이 호출 → 항상 "재생을 실패하였습니다" 전송 |

- `player.py` `_notify_playback_error()`에서 `notify_playback_error()`를 `failed` 없이 호출
- "사용자 중단" 케이스는 애초에 알림 자체를 보내지 않으므로 구분이 필요한 케이스는 `final_state.error` 유무

---

### 1-C. 자동 모드 사이클 내 중간 브라우저 재시작 (5강의마다) 없음

| | study-helper | study-dashboard |
|---|---|---|
| 위치 | `src/ui/auto.py:258-265` | `backend/api/routes/auto.py:_auto_loop` |
| 동작 | 사이클 내 5강의마다(`idx % 5 == 0`) 브라우저 재시작 | 사이클 **종료 후**에만 재시작 |

- 장시간 자동 모드에서 강의가 많을 경우 사이클 종료 전에 Chromium 힙이 누적될 수 있음
- `_run_auto_cycle()` 루프 내에 5강의 단위 중간 재시작 로직이 없음

---

### 1-D. 수동 다운로드 task 완료 후 텔레그램 알림 없음

| | study-helper | study-dashboard |
|---|---|---|
| 위치 | `src/ui/auto.py:_process_lecture` (다운로드 완료 후 TG 전송) | `backend/api/routes/tasks.py:start_download` |
| 동작 | 요약 완료 시 텔레그램으로 요약 문서 전송 | event_log만 기록, 텔레그램 알림 없음 |

- 자동 모드에서는 `_run_post_play_pipeline()`에서 `notify_summary_complete()` 호출 ✅
- 수동 다운로드 task (`POST /api/tasks/download`)에는 텔레그램 전송 없음

---

## 2. study-dashboard 설계 목표에 있으나 아직 미구현인 기능

### 2-A. 로그인 직후 자동 마감 임박 알림 체크

| | study-helper | study-dashboard |
|---|---|---|
| 위치 | `src/main.py:88-94` | `frontend/js/app.js`, `backend/api/routes/deadline.py` |
| 동작 | 과목 목록 로드 직후 자동으로 마감 임박 체크 + 텔레그램 전송 | **수동 "마감 확인" 버튼만** (`#btn-deadline-check`). 로그인 후 자동 호출 없음 |

- 성공 기준: 로그인 + 과목 목록 로드 완료 시 `POST /api/deadline/check`를 백엔드가 자동 실행하거나, 프론트엔드 로그인 성공 콜백에서 자동 호출

---

### 2-B. 과제/퀴즈 상세 목록 뷰

- 대시보드 통계 카드에 "제출 필요 과제 N개", "제출 필요 퀴즈 N개"가 표시되나 클릭해도 상세 목록 없음
- `GET /api/courses` 응답에 `pending_assignment_count`, `pending_quiz_count` 데이터는 있음
- 성공 기준: 통계 카드 클릭 시 미제출 항목 목록(과목, 항목명, 마감일) 페이지 또는 모달 표시

---

### 2-C. 수동 재생 완료 후 자동 다운로드가 프론트엔드 polling에 의존

- `AUTO_DOWNLOAD_AFTER_PLAY` 설정 기반 자동 다운로드는 `app.js:195-208`에서 polling 중 `completed` 감지 시 시작
- 브라우저 탭이 닫히거나 네트워크가 끊기면 자동 다운로드가 트리거되지 않음
- 성공 기준: 재생 완료 이벤트를 백엔드(`player.py`)에서 감지하여 다운로드 task를 직접 생성

---

### 2-D. STT / 요약 결과 파일 다운로드 버튼 없음

- STT 텍스트: `GET /api/tasks/{task_id}/stt`로 내용을 조회하여 모달에 표시 ✅
- AI 요약: `GET /api/summaries/{summary_id}`로 조회 후 모달 렌더링 ✅
- **파일 자체를 다운로드하는 버튼 없음** — 긴 STT 텍스트나 요약 파일을 로컬에 저장할 방법이 없음
- 성공 기준: 모달 헤더에 "파일 다운로드" 버튼 → `Content-Disposition: attachment` 응답 엔드포인트 추가

---

### 2-E. 강의 목록 필터 / 검색 없음

- 강의 목록 페이지에서 완료/미완료 필터, 강의명 검색 없음
- 과목이 많아질수록 원하는 강의를 찾기 어려움
- 성공 기준: "미시청만 보기" 토글 및 강의명 인라인 검색 (`frontend/js/app.js`에서 클라이언트 사이드 필터링)

---

### 2-F. 초기 설정 강제 진입 없음

- study-helper: 설정 미완료 시 `run_settings()` 자동 호출 (`main.py:73-74`)
- study-dashboard: 설정 없이도 앱 정상 진입 가능 — AI 요약/다운로드 설정이 비어 있어도 경고 없음
- 성공 기준: 로그인 직후 `GET /api/settings` 결과를 검사하여 필수 미설정 항목(`DOWNLOAD_ENABLED`, `AI_ENABLED` 등) 있으면 설정 화면으로 자동 이동 또는 배너 표시

---

### 2-G. 재생 상태 / 자동 모드 상태 폴링 → WebSocket 전환 (성능)

- 현재 `GET /api/player/status`, `GET /api/auto/status`를 1-2초 주기로 반복 polling
- 재생 중이 아닐 때도 polling이 계속 실행됨
- 성공 기준: `/ws/status` WebSocket 엔드포인트로 전환하여 상태 변경 시에만 push

---

## 3. 우선순위 요약

| 우선순위 | 항목 | 분류 | 난이도 |
|---------|------|------|--------|
| P0 | 1-A 수동 재생 완료 텔레그램 알림 | 미이식 | 낮음 (1줄) |
| P0 | 1-B 재생 실패 vs 미완료 구분 | 미이식 | 낮음 (파라미터 추가) |
| P1 | 2-A 로그인 후 자동 마감 체크 | 미구현 | 낮음 |
| P1 | 1-D 수동 다운로드 텔레그램 알림 | 미이식 | 낮음 |
| P1 | 1-C 자동 모드 5강의마다 재시작 | 미이식 | 낮음 |
| P2 | 2-C 자동 다운로드 백엔드 처리 | 미구현 | 중간 |
| P2 | 2-B 과제/퀴즈 상세 목록 | 미구현 | 중간 |
| P2 | 2-D STT/요약 파일 다운로드 | 미구현 | 낮음 |
| P2 | 2-E 강의 필터/검색 | 미구현 | 낮음 |
| P3 | 2-F 초기 설정 강제 진입 | 미구현 | 낮음 |
| P3 | 2-G WebSocket 전환 | 미구현 | 높음 |

---

## 4. 구현 시 참고 파일

| 항목 | 참고 위치 |
|------|----------|
| 1-A 완료 알림 | `src/notifier/telegram_notifier.py:notify_playback_complete`, `backend/api/routes/player.py:run()` |
| 1-B 실패 구분 | `backend/api/routes/player.py:_notify_playback_error`, `telegram_notifier.py:notify_playback_error(failed=)` |
| 1-C 중간 재시작 | `src/ui/auto.py:258-265`, `backend/api/routes/auto.py:_run_auto_cycle()` |
| 1-D 다운로드 TG | `backend/api/routes/auto.py:_run_post_play_pipeline`, `backend/api/routes/tasks.py:start_download` |
| 2-A 마감 자동 체크 | `src/main.py:88-94`, `backend/api/routes/deadline.py`, `frontend/js/app.js` 로그인 성공 콜백 |
| 2-B 과제/퀴즈 목록 | `backend/api/routes/courses.py`, `src/scraper/models.py`, `frontend/index.html` |
| 2-C 백엔드 자동 다운로드 | `backend/api/routes/player.py:run()` 완료 분기, `backend/api/routes/tasks.py:start_download` |
| 2-D 파일 다운로드 | `backend/api/routes/tasks.py:get_stt_text`, `backend/api/routes/summaries.py` |
| 2-E 필터/검색 | `frontend/js/app.js:loadCourseDetail()`, `frontend/index.html` |
