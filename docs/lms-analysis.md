# Learning X 구조 분석 정의서

숭실대학교 Canvas Learning X(`canvas.ssu.ac.kr`) 연동 방식 및 각 기능의 구현 분석 문서.

---

## 목차

1. [Learning X 전체 구조](#1-learning-x-전체-구조)
2. [인증 (로그인)](#2-인증-로그인)
3. [과목 목록 수집](#3-과목-목록-수집)
4. [강의 목록 수집](#4-강의-목록-수집)
5. [백그라운드 재생 (출석 처리)](#5-백그라운드-재생-출석-처리)
6. [영상 다운로드](#6-영상-다운로드)
7. [브라우저 설정 (헤드리스 위장)](#7-브라우저-설정-헤드리스-위장)

---

## 1. Learning X 전체 구조

```
canvas.ssu.ac.kr (Learning X 메인)
│
├── 대시보드 (/)
│   └── window.ENV.STUDENT_PLANNER_COURSES  ← 과목 목록 데이터
│
├── 강의 목록 (/courses/{id}/external_tools/71)
│   └── iframe#tool_content
│       └── iframe (commons.ssu.ac.kr/em/...)  ← 강의 목록 SPA
│           └── #root[data-course_name][data-professors]
│               └── .xnmb-module-list
│                   └── .xnmb-module_item-outer-wrapper (강의 항목)
│
└── 강의 페이지 (/courses/{id}/modules/items/{item_id})
    └── iframe#tool_content (name="tool_content")
        ├── [일반] iframe (commons.ssu.ac.kr/em/{content_id}?...)
        │   ├── 플레이어 화면 (.vc-front-screen-play-btn)
        │   └── 영상 재생 화면 (video.vc-vplay-video1)
        └── [learningx] iframe (canvas.ssu.ac.kr/learningx/...)
            └── → learningx API로 viewer_url 획득 후 commons 방식으로 처리
```

### 핵심 URL 패턴

| 항목 | URL 패턴 |
|------|----------|
| 대시보드 | `https://canvas.ssu.ac.kr/` |
| 강의 목록 | `https://canvas.ssu.ac.kr/courses/{course_id}/external_tools/71` |
| 강의 페이지 | `https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{item_id}` |
| 플레이어 | `https://commons.ssu.ac.kr/em/{content_id}?endat=...&TargetUrl=...&sl=1` |
| 진도 API | `TargetUrl` 디코딩값 (canvas.ssu.ac.kr/learningx/... 형태) |
| 영상 CDN | `https://commonscdn.com/...` 또는 `https://ssu-toast.ssu.ac.kr/...` |
| 강의 설정 | `https://commons.ssu.ac.kr/.../content.php` |

---

## 2. 인증 (로그인)

**파일:** `src/auth/login.py`

### 흐름

```
canvas.ssu.ac.kr → SSO 로그인 페이지 리디렉션
→ .login_btn a 클릭 (SSO 폼 진입)
→ input#userid + input#pwd 입력
→ a.btn_login 클릭
→ 폴링으로 성공/실패 판정 (최대 10초, 0.25초 간격)
  ├── 성공: canvas.ssu.ac.kr URL + "login" 없음 + .login_btn 없음
  └── 실패: JS 다이얼로그 감지 | 오류 문구 감지 | 로그인 폼 3초 이상 유지 | 타임아웃
```

### 핵심 셀렉터

| 역할 | 셀렉터 |
|------|--------|
| 로그인 버튼 진입 | `.login_btn a` |
| 학번 입력란 | `input#userid` |
| 비밀번호 입력란 | `input#pwd` |
| 로그인 제출 버튼 | `a.btn_login` |
| 로그인 필요 감지 | `.login_btn` (visible 여부) |

### 코드

```python
# src/auth/login.py: perform_login()
page.on("dialog", _on_dialog)  # JS 다이얼로그 자동 수락 + 실패 감지

login_button = await page.query_selector(".login_btn a")
if login_button:
    await login_button.click()
    await page.wait_for_selector("input#userid", timeout=10_000)

await page.fill("input#userid", username)
await page.fill("input#pwd", password)
await page.click("a.btn_login")

return await _wait_for_login_result(page, dialog_seen)
# 실패 조건: JS 다이얼로그 | "로그인 실패" 등 오류 텍스트 | 폼 3초 유지 | 10초 타임아웃
```

> `ensure_logged_in(page, username, password)` — `_needs_login()`으로 상태 확인 후 필요 시에만 `perform_login()` 호출하는 공개 진입점.

---

## 3. 과목 목록 수집

**파일:** `src/scraper/course_scraper.py` → `fetch_courses()`

### 구조

Learning X 대시보드는 Canvas Learning X 기반으로, 수강 과목 목록을 **JavaScript 전역 변수** `window.ENV.STUDENT_PLANNER_COURSES`에 JSON 배열로 주입한다.

별도 API 엔드포인트 호출 없이 이 변수를 `page.evaluate()`로 직접 읽는다.

### 데이터 구조 (예시)

```json
[
  {
    "id": 12345,
    "longName": "소프트웨어공학 - 소프트웨어공학",
    "href": "/courses/12345",
    "term": "2025-1",
    "isFavorited": false
  }
]
```

### 전처리

- `term`이 없는 항목(비교과, 안내 과목)은 제외
- `longName`이 `"과목명 - 과목명"` 형태로 중복 반환되는 경우 앞쪽만 사용

### 코드

```python
# src/scraper/course_scraper.py
raw = await self._page.evaluate(
    "() => window.ENV && window.ENV.STUDENT_PLANNER_COURSES"
)
```

---

## 4. 강의 목록 수집

**파일:** `src/scraper/course_scraper.py` → `fetch_lectures()`, `_parse_weeks()`, `_parse_item()`

### iframe 구조

강의 목록은 Learning X가 외부 도구(external_tools/71)로 삽입한 SPA이다. 두 단계의 iframe을 거쳐야 실제 DOM에 접근할 수 있다.

```
Learning X 페이지
└── iframe#tool_content
    └── commons.ssu.ac.kr iframe
        └── #root (data-course_name, data-professors 속성)
            └── 강의 목록 SPA (.xnmb-*)
```

### DOM 구조 및 셀렉터

| 항목 | 셀렉터 |
|------|--------|
| 루트 컨테이너 | `#root` |
| 전체 펼치기 버튼 | `.xnmb-all_fold-btn` |
| 주차 목록 컨테이너 | `.xnmb-module-list` |
| 주차 헤더 | `.xnmb-module-outer-wrapper` |
| 주차 제목 | `.xnmb-module-title` |
| 강의 항목 | `.xnmb-module_item-outer-wrapper` |
| 강의 아이콘 (타입 판별) | `i.xnmb-module_item-icon` |
| 강의 제목/링크 | `a.xnmb-module_item-left-title` |
| 영상 길이 | `[class*='lecture_periods'] span` |
| 주차 레이블 | `[class*='lesson_periods-week']` |
| 시작일 | `[class*='lecture_periods-unlock_at'] span` |
| 마감일 | `[class*='lecture_periods-due_at'] span` |
| 출석 상태 | `[class*='attendance_status']` |
| 수강 완료 여부 | `[class*='module_item-completed']` |
| D-day (미개방) | `.xncb-component-sub-d_day` |

### 강의 타입 분류

아이콘 요소(`i.xnmb-module_item-icon`)의 CSS 클래스명으로 강의 타입을 판별한다.

| CSS 클래스 | LectureType | 비고 |
|-----------|-------------|------|
| `movie` | MOVIE | 일반 강의 영상 |
| `readystream` | READYSTREAM | 스트리밍 영상 |
| `screenlecture` | SCREENLECTURE | 화면 녹화 강의 |
| `everlec` | EVERLEC | Everlec 플레이어 |
| `mp4` | MP4 | 직접 MP4 |
| `zoom` | ZOOM | Zoom 녹화 |
| `assignment` | ASSIGNMENT | 과제 |
| `wiki_page` | WIKI_PAGE | 위키 페이지 |
| `quiz` | QUIZ | 퀴즈 |
| `discussion` | DISCUSSION | 토론 |
| `file`/`attachment` | FILE | 파일 |

영상으로 처리 가능한 타입: `MOVIE`, `READYSTREAM`, `SCREENLECTURE`, `EVERLEC`, `MP4`

### 출석/완료 상태 판별

실제 DOM (canvas.ssu.ac.kr Learning X, 2026-09 캡처):

```html
<!-- 출석됨 -->
<span class="xnmb-module_item-meta_data-attendance_status attendance">출석</span>
<!-- 출석 안 됨 -->
<span class="xnmb-module_item-meta_data-attendance_status none">-</span>
<!-- 출석 추적 안 하는 항목: 요소 자체가 없음 -->

<!-- 완료 -->
<span class="xnmb-module_item-completed completed">완료<i>…</i></span>
<!-- 미완료 -->
<span class="xnmb-module_item-completed incomplete">-</span>
```

상태 수식어(`attendance`/`none`/`completed`/`incomplete`)는 **별도 토큰**이다.
기저 클래스명(`…attendance_status`, `module_item-completed`)에 `attendance`·`completed`가
부분 문자열로 들어 있으므로, **부분 문자열 검색을 쓰면 상태가 `none`/`incomplete`여도
잘못 매칭된다** (v26.10.4 이전 버그). 반드시 공백 분리 토큰 정확 매칭으로 판별한다:

```python
# src/scraper/course_scraper.py::_parse_attendance_class / _parse_completion_class
tokens = set(class_attr.split())
attendance = next((s for s in ("attendance", "late", "absent", "excused") if s in tokens), "none")
completion = "completed" if "completed" in class_attr.split() else "incomplete"
```

---

## 5. 백그라운드 재생

**파일:** `src/player/background_player.py` → `play_lecture()`

Learning X의 출석 처리는 영상 플레이어가 진도 API(`TargetUrl`)에 재생 진행 상황을 주기적으로 보고하는 방식으로 동작한다. 이를 headless 브라우저 환경에서 재현한다.

### 재생 전략: Plan A → Plan B 자동 전환

```
강의 페이지 접속
→ [플레이어 선택 화면 frame 탐색]
   ├── 성공: Plan A (video DOM 폴링)
   │   └── 실패 시 Plan B로 자동 전환
   └── 실패: learningx 감지 여부 확인
       ├── learningx: learningx API → Plan B
       └── 그 외: 오류 반환
```

### Plan A: video DOM 폴링

브라우저가 실제로 영상을 재생하고, `video.vc-vplay-video1`의 `currentTime`을 폴링하여 진행 상황을 추적한다.

#### 재생 흐름

```
1. 강의 페이지 이동 (wait_until="domcontentloaded")
2. tool_content → commons.ssu.ac.kr frame 탐색 (최대 30초)
3. 이어보기 다이얼로그 처리 → 처음부터 재생 (.confirm-cancel-btn 클릭)
4. 재생 버튼 클릭 (.vc-front-screen-play-btn)
5. video 태그가 있는 frame 재스캔 (재생 후 frame 구조 변경 대응)
6. video.duration > 0이 될 때까지 대기 (최대 20초)
7. video.currentTime 폴링 (1초 간격)
   ├── 일시정지 감지 → JS로 강제 재생
   └── currentTime >= duration - 3초 → 완료
8. 완료 보고: progress API에 100% 진도 직접 전송
```

#### 핵심 셀렉터

| 역할 | 셀렉터 |
|------|--------|
| 이어보기 다이얼로그 | `.confirm-msg-box` |
| 이어보기 버튼 | `.confirm-ok-btn` |
| 처음부터 버튼 | `.confirm-cancel-btn` |
| 재생 버튼 | `.vc-front-screen-play-btn` |
| 비디오 요소 | `video.vc-vplay-video1` |

#### H.264 우회 (항상 적용)

Chromium headless는 H.264(mp4)를 지원하지 않아 플레이어가 `flashErrorPage.html`로 분기한다. 환경(ARM64, Docker, Apple Silicon 등)과 무관하게 항상 우회 경로를 등록한다.

1. ffmpeg으로 VP8 WebM 더미 영상 lazy 생성 (2×2 픽셀, 강의 길이만큼 — MP4 요청 시점에 생성)
2. `page.route("**/*.mp4", ...)` — 모든 MP4 요청을 WebM으로 교체
3. `canPlayType` / `MediaSource.isTypeSupported` init script 오버라이드 — 플레이어가 MP4를 요청하도록 유도
4. `GetCurrentTime` / `GetTotalDuration` / `sendPlayedTime` 오버라이드 — 가짜 영상의 재생 시간이 진도 API에 올바르게 전달되도록 보정
5. `isPlayedContent = true` / `afterPlayStateChange('play')` 강제 호출 — 플레이어 JS가 START 이벤트를 발생시키지 않는 경우 보완

```python
# 더미 영상 생성 (MP4 요청 시 lazy 생성, 캐시 재사용)
await asyncio.create_subprocess_exec(
    "ffmpeg", "-f", "lavfi", "-i", "color=black:s=2x2:r=1",
    "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
    "-t", dur, "-c:v", "libvpx", "-b:v", "1k",
    "-c:a", "libopus", "-b:a", "8k", output_path
)

# MP4 요청 인터셉트 (항상 등록)
await page.route("**/*.mp4", _serve_fake)
```

#### 사전 처리 라우트 및 리스너 (page.goto 이전 등록)

| 이름 | 대상 | 목적 |
|------|------|------|
| `_sniff_attendance_duration` | `response` 이벤트 | `attendance_items` / `lecture_attendance` 응답에서 `duration` 미리 추출 — MP4 요청 전에 올바른 길이를 확보 |
| `_fix_commons_endat` | `**/commons.ssu.ac.kr/em/**` | 스니핑된 `duration`으로 `endat` 파라미터 교정 — 이전 시청 위치로 고정된 경우 전체 길이로 재설정 |
| `_block_flash_global` | `**/flashErrorPage.html` | `flashErrorPage` 응답을 빈 HTML로 교체 — H.264 실패 시 sl=1 세션이 무효화되는 것을 처음부터 방지 |

### Plan B: 진도 API 직접 호출

video DOM 접근이 불가한 경우, 플레이어 URL의 `TargetUrl` 파라미터에서 진도 API URL을 추출해 직접 호출한다.

#### player URL 구조

```
https://commons.ssu.ac.kr/em/{content_id}
    ?endat={duration_sec}
    &TargetUrl={URL-encoded progress API URL}
    &sl=1
    ...
```

#### 진도 API 파라미터

```
{progress_url}?callback={cb}
    &state=3
    &duration={total_sec}
    &currentTime={current_sec}
    &cumulativeTime={current_sec}
    &page={current_page}
    &totalpage=15
    &cumulativePage={current_page}
    &_={timestamp_ms}
```

- `state=3`: 재생 중 상태
- `totalpage=15`: Learning X 플레이어 기본값 (전체를 15 페이지로 나눔)
- 30초 간격으로 보고, 최종 완료 시 `currentTime=duration`으로 보고

#### ErrAlreadyInView 우회

`sl=1` 파라미터로 commons 뷰 세션이 열린 상태에서 외부에서 직접 호출하면 `ErrAlreadyInView` 오류가 반환된다. 진도 보고는 아래 3단계 순서로 시도한다.

| 순서 | 방법 | 조건 |
|------|------|------|
| 1 | `page.evaluate` fetch — canvas.ssu.ac.kr 동일 오리진으로 요청 | 항상 시도 (쿠키 자동 포함) |
| 2 | JSONP script 태그 주입 (commons_frame 내부) | 1 실패 + commons frame 살아있음 |
| 3 | `page.request.get()` | 1, 2 모두 실패 시 최후 수단 |

Plan A에서 넘어온 경우 기존 sl=1 commons frame을 재사용(`existing_commons_frame`)하여 sl=0 재로드 없이 JSONP를 주입한다.

### learningx 타입 강의

`tool_content` frame URL이 `canvas.ssu.ac.kr/learningx/...`인 경우, learningx API에서 `viewer_url`을 획득하여 commons 플레이어 URL로 변환 후 Plan B 방식으로 처리한다.

```python
# learningx API 호출
api_url = f"https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/attendance_items/{item_id}"
result = await page.evaluate(f"async () => {{ const resp = await fetch('{api_url}'); return await resp.json(); }}")

# viewer_url에서 commons TargetUrl 추출 → Plan B 실행
viewer_url = result["viewer_url"]
duration = result["item_content_data"]["duration"]
```

---

## 6. 영상 다운로드

**파일:** `src/downloader/video_downloader.py`

### 영상 URL 추출 (`extract_video_url`)

강의 영상의 실제 CDN URL을 추출하는 과정이다. 두 가지 방법을 병행한다.

#### 방법 1: content.php XML 파싱 (우선)

강의 페이지 로드 시 `commons.ssu.ac.kr/...content.php`로 요청이 발생하며, 이 XML 응답에 미디어 URL이 포함된다.

**구조 A (최신 강의):**
```xml
<content_playing_info>
  <main_media>
    <desktop>
      <html5>
        <media_uri>https://commonscdn.com/.../lecture.mp4</media_uri>
      </html5>
    </desktop>
  </main_media>
</content_playing_info>
```

**구조 B (구형 강의):**
```xml
<service_root>
  <media>
    <media_uri method="progressive">https://cdn.../[MEDIA_FILE]</media_uri>
  </media>
</service_root>
<story_list>
  <story>
    <main_media_list>
      <main_media>lecture_01.mp4</main_media>
    </main_media_list>
  </story>
</story_list>
```

구조 B의 경우 `[MEDIA_FILE]` 플레이스홀더를 `main_media` 텍스트로 치환하여 실제 URL 생성.

```python
# src/downloader/video_downloader.py
def _on_response(response):
    if "content.php" in url and "commons.ssu.ac.kr" in url:
        async def _parse_content_php():
            root = ET.fromstring(await response.text())
            # 구조 A 우선
            for path in (
                "content_playing_info/main_media/desktop/html5/media_uri",
                "content_playing_info/main_media/mobile/html5/media_uri",
                ".//main_media//html5/media_uri",
            ):
                el = root.find(path)
                if el is not None and el.text and "[" not in el.text:
                    media_uri = el.text.strip()
                    break
            # 구조 B fallback
            if not media_uri:
                # [MEDIA_FILE] 플레이스홀더 치환
                ...
```

#### 방법 2: Network 요청/응답 캡처 + video DOM 폴링 (fallback)

content.php에서 URL을 찾지 못한 경우:

1. `page.on("request"/"response")` 리스너로 `.mp4` URL 캡처 (단, `preloader.mp4`, `preview.mp4`, `thumbnail.mp4` 제외)
2. 재생 버튼 클릭 후 `commons.ssu.ac.kr` frame의 `video.vc-vplay-video1.src` 폴링 (최대 60초)

### 영상 다운로드 (`download_video_with_browser`)

Playwright `response.body()`는 Node.js 내부 문자열 크기 제한(`0x1fffffe8 ≈ 512MB`)으로 대용량 파일에서 크래시가 발생한다. 이를 우회하기 위해 `requests` 라이브러리로 스트리밍 다운로드한다.

```python
# Playwright 컨텍스트에서 쿠키 추출
context_cookies = await page.context.cookies()
cookies = {c["name"]: c["value"] for c in context_cookies}

# requests로 스트리밍 다운로드 (64KB 청크)
response = requests.get(url, stream=True, cookies=cookies,
                        headers={"Referer": "https://commons.ssu.ac.kr/"})
with open(save_path, "wb") as f:
    for chunk in response.iter_content(chunk_size=65536):
        f.write(chunk)
```

### 재시도 로직

| 단계 | 재시도 조건 | 횟수 | 대기 |
|------|-----------|------|------|
| URL 추출 | 항상 | 최대 3회 | 10초 고정 |
| HTTP 다운로드 | CDN 오류 (504 등) | 최대 3회 | 2초, 4초 (지수 백오프) |

### 영상 CDN 타입

| CDN | 도메인 | 비고 |
|-----|--------|------|
| ssuin-object | `commonscdn.com` | 일반 강의 |
| ssu-toast | `ssu-toast.ssu.ac.kr` | 대용량 강의 (390MB+) |

---

## 7. 브라우저 설정 (헤드리스 위장)

**파일:** `src/scraper/course_scraper.py` → `_setup_browser()`

Learning X가 headless 브라우저를 차단하는 경우를 대비해 일반 Chrome처럼 보이도록 위장한다.

### Chromium 실행 인수

| 인수 | 목적 |
|------|------|
| `--disable-blink-features=AutomationControlled` | 자동화 탐지 방지 |
| `--enable-proprietary-codecs` | 독점 코덱 활성화 시도 |
| `--disable-web-security` | 크로스오리진 요청 허용 (진도 API 호출) |
| `--use-fake-ui-for-media-stream` | 미디어 스트림 권한 팝업 억제 |
| `--no-sandbox` / `--disable-setuid-sandbox` | Docker 환경 대응 |
| `--disable-dev-shm-usage` | Docker 공유 메모리 부족 방지 |
| `--disable-accelerated-2d-canvas` | 렌더링 안정성 |
| `--no-first-run` / `--no-zygote` | 초기화 프로세스 생략 |
| `--disable-gpu` | headless 안정성 |
| `--window-size=1280,720` | 뷰포트 고정 |
| `--password-store=basic` | macOS Keychain 접근 경고 제거 |
| `--js-flags=--max-old-space-size=1024` | JS 힙 상한 설정 (메모리 누수 방지) |
| `--aggressive-cache-discard` | 메모리 압박 시 캐시 조기 해제 |
| `--renderer-process-limit=2` | 렌더러 프로세스 수 제한 (메모리 절약) |

### JS 위장 (init script)

```javascript
// webdriver 속성 제거 (봇 탐지 방지)
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// chrome 런타임 위장
window.chrome = { runtime: {}, ... };

// plugins 위장 (headless에서는 빈 배열 → 유사 실제값으로 대체)
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

// 언어 위장
Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
```

### Chrome 채널 우선 사용

H.264 내장 지원을 위해 시스템 Chrome을 우선 사용하고, 미설치 환경(Docker)에서는 Playwright 내장 Chromium으로 fallback한다.

```python
try:
    browser = await pw.chromium.launch(headless=True, channel="chrome", args=_args)
except Exception:
    browser = await pw.chromium.launch(headless=True, args=_args)  # fallback
```

---

## 부록: 강의 타입별 처리 흐름 요약

| 강의 타입 | 플레이어 URL | 재생 방법 | 다운로드 가능 |
|-----------|------------|----------|-------------|
| 일반 (movie/screenlecture 등) | `commons.ssu.ac.kr/em/...` | Plan A → Plan B | O (content.php 구조 A/B) |
| learningx | `canvas.ssu.ac.kr/learningx/...` | learningx API → Plan B | X |
| Zoom | - | - | X |
| MP4 직접 | `commons.ssu.ac.kr/em/...` | Plan A → Plan B | O |
