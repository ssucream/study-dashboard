import asyncio
import contextlib
import re
import sys
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from playwright.async_api import Frame, Page, async_playwright

from src.auth.login import _needs_login, ensure_logged_in
from src.scraper.models import (
    Course,
    CourseDetail,
    LectureItem,
    LectureType,
    Week,
)

_BASE_URL = "https://canvas.ssu.ac.kr"
_DASHBOARD_URL = f"{_BASE_URL}/"

_ATTENDANCE_STATUSES = ("attendance", "late", "absent", "excused")


def _parse_attendance_class(class_attr: str) -> str:
    """출석 상태 요소의 class 속성에서 상태 수식어를 뽑는다.

    class는 "xnmb-module_item-meta_data-attendance_status <상태>" 형태이며 <상태>는
    별도 토큰이다. 기저 클래스명에 "attendance" 부분 문자열이 들어 있으므로
    부분 문자열 검색이 아니라 공백 분리 토큰 정확 매칭으로 판별해야 한다
    (상태가 `none`이어도 부분 문자열 검색은 "attendance"로 오판한다).
    """
    tokens = set(class_attr.split())
    for status in _ATTENDANCE_STATUSES:
        if status in tokens:
            return status
    return "none"


def _parse_completion_class(class_attr: str) -> str:
    """완료 여부 요소의 class 속성에서 완료 상태를 뽑는다.

    class는 "xnmb-module_item-completed <상태>" 형태이며 <상태>는 `completed` 또는
    `incomplete`. 기저 클래스명이 "completed"를 부분 문자열로 포함하므로 토큰 매칭한다.
    """
    return "completed" if "completed" in class_attr.split() else "incomplete"


_TYPE_CLASS_MAP = {
    "movie": LectureType.MOVIE,
    "readystream": LectureType.READYSTREAM,
    "screenlecture": LectureType.SCREENLECTURE,
    "everlec": LectureType.EVERLEC,
    "zoom": LectureType.ZOOM,
    "mp4": LectureType.MP4,
    "assignment": LectureType.ASSIGNMENT,
    "wiki_page": LectureType.WIKI_PAGE,
    "quiz": LectureType.QUIZ,
    "discussion": LectureType.DISCUSSION,
    "file": LectureType.FILE,
    "attachment": LectureType.FILE,
}


class CourseScraper:
    def __init__(
        self,
        username: str,
        password: str,
        headless: bool = True,
        log_callback: Callable[[str], None] | None = None,
    ):
        self.username = username
        self.password = password
        self.headless = headless
        self._log = log_callback or (lambda msg: None)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._login_lock = asyncio.Lock()
        self._session_restored = False  # 병렬 재로그인 중복 방지 플래그

    async def _setup_browser(self, storage_state: dict | None = None):
        _args = [
            "--disable-blink-features=AutomationControlled",
            "--enable-proprietary-codecs",
            "--disable-web-security",
            "--use-fake-ui-for-media-stream",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--no-zygote",
            "--disable-gpu",
            "--window-size=1280,720",
            "--password-store=basic",
            # 메모리 누수 방지: JS 힙 상한 · 메모리 압박 시 캐시 해제 · 렌더러 수 제한
            "--js-flags=--max-old-space-size=1024",
            "--aggressive-cache-discard",
            "--renderer-process-limit=2",
        ]
        # Chrome(H.264 포함) 우선 시도 — ARM64 등 미지원 환경에서는 Chromium으로 fallback
        try:
            browser = await self._pw.chromium.launch(
                headless=self.headless,
                channel="chrome",
                args=_args,
            )
        except Exception:
            browser = await self._pw.chromium.launch(
                headless=self.headless,
                args=_args,
            )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            permissions=["camera", "microphone", "geolocation"],
            viewport={"width": 1280, "height": 720},
            storage_state=storage_state,
        )
        await context.add_init_script("""
            // webdriver 속성 제거
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // chrome 런타임 위장
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // plugins 위장 (headless에서는 빈 배열)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });

            // languages 위장
            Object.defineProperty(navigator, 'languages', {
                get: () => ['ko-KR', 'ko', 'en-US', 'en'],
            });

            // permissions 위장
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications'
                    ? Promise.resolve({ state: Notification.permission })
                    : originalQuery(parameters)
            );
        """)
        page = await context.new_page()
        self._context = context
        return page, browser

    async def start(self, storage_state: dict | None = None):
        """브라우저를 시작하고 로그인한다.

        storage_state가 주어지면 (백엔드 재시작 후 저장된 세션 쿠키로 무인 재개)
        해당 쿠키로 컨텍스트를 만들고 로그인을 건너뛴다. 쿠키가 이미 만료됐고
        username/password도 없으면(순수 쿠키 재개 시도) 재로그인할 수 없으므로
        예외를 던져 호출자가 "세션 만료 → 수동 로그인 필요"로 처리하게 한다.
        """
        self._pw = await async_playwright().start()
        self._page, self._browser = await self._setup_browser(storage_state=storage_state)
        self._log("LMS 접속 중...")
        await self._page.goto(_DASHBOARD_URL, wait_until="domcontentloaded")
        if await _needs_login(self._page):
            if not self.username or not self.password:
                raise RuntimeError("저장된 세션이 만료되었습니다. 다시 로그인해주세요.")
            self._log("로그인 진행 중...")
            ok = await ensure_logged_in(self._page, self.username, self.password)
            if not ok:
                raise RuntimeError("로그인 실패. 학번/비밀번호를 확인하세요.")
            self._log("로그인 완료")

    async def export_storage_state(self) -> dict:
        """현재 세션 쿠키를 반환한다 (백엔드 재시작 후 무인 재개를 위해 암호화 저장할 원본)."""
        if self._context is None:
            raise RuntimeError("브라우저가 시작되지 않았습니다.")
        return await self._context.storage_state()

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def new_page(self) -> Page:
        """로그인 쿠키를 승계한 새 page를 만든다.

        강의를 재생할 때마다 새 page를 쓰면 add_init_script 누적·SPA 잔여 상태 같은
        page 오염이 다음 강의로 번지지 않는다 (`fetch_all_details`와 같은 패턴).
        """
        if self._context is None:
            raise RuntimeError("브라우저가 시작되지 않았습니다.")
        return await self._context.new_page()

    async def close_page(self, page: Page) -> None:
        """new_page()로 만든 page를 폐기한다.

        about:blank 선행 이동은 commons 뷰 세션(sl=1)을 정리해 다음 강의에서
        ErrAlreadyInView가 발생하지 않게 하려는 것이다. 정리 실패는 무시한다.
        """
        with contextlib.suppress(Exception):
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
        with contextlib.suppress(Exception):
            await page.close()

    async def fetch_courses(self) -> list[Course]:
        """대시보드에서 수강 과목 목록 추출"""
        if "canvas.ssu.ac.kr" not in self._page.url or "/courses/" in self._page.url:
            await self._page.goto(_DASHBOARD_URL, wait_until="domcontentloaded")

        # 세션 만료 시 자동 재로그인
        if await _needs_login(self._page):
            await self._ensure_session()
            await self._page.goto(_DASHBOARD_URL, wait_until="domcontentloaded")

        # STUDENT_PLANNER_COURSES는 JS로 주입되므로 변수가 채워질 때까지 대기
        await self._page.wait_for_function(
            "() => Array.isArray(window.ENV && window.ENV.STUDENT_PLANNER_COURSES)",
            timeout=15000,
        )
        raw = await self._page.evaluate("() => window.ENV && window.ENV.STUDENT_PLANNER_COURSES")
        if not raw:
            raise RuntimeError("과목 목록을 불러올 수 없습니다.")

        raw_courses = []
        term_counts: dict[str, int] = {}
        for item in raw:
            # 학기 정보가 없는 비교과(안내 등) 과목 제외
            term = item.get("term", "")
            if not term:
                continue

            long_name = item.get("longName", "")
            # Learning X API가 "과목명 - 과목명" 형태로 중복 반환하는 경우 앞쪽만 사용
            if " - " in long_name:
                first, _, second = long_name.partition(" - ")
                if first.strip() == second.strip():
                    long_name = first.strip()
            raw_courses.append(
                Course(
                    id=str(item["id"]),
                    long_name=long_name,
                    href=item.get("href", f"/courses/{item['id']}"),
                    term=term,
                    is_favorited=item.get("isFavorited", False),
                )
            )
            term_counts[term] = term_counts.get(term, 0) + 1

        # 가장 많이 등장하는 term을 현재 학기로 간주하여 해당 학기 과목만 반환
        # (이전 학기 과목이 즐겨찾기 등으로 남아있는 경우 제외)
        if term_counts:
            current_term = max(term_counts, key=lambda t: term_counts[t])
            courses = [c for c in raw_courses if c.term == current_term]
        else:
            courses = raw_courses
        return courses

    async def _ensure_session(self) -> None:
        """세션 만료 시 자동 재로그인을 시도한다."""
        if await _needs_login(self._page):
            self._log("세션 만료 감지 — 자동 재로그인 중...")
            ok = await ensure_logged_in(self._page, self.username, self.password)
            if not ok:
                raise RuntimeError("자동 재로그인 실패. 학번/비밀번호를 확인하세요.")
            self._log("재로그인 완료")

    async def fetch_lectures(self, course: Course) -> CourseDetail:
        """과목의 주차별 강의 목록 스크래핑 (메인 페이지 사용)"""
        return await self._fetch_lectures_on(self._page, course)

    async def fetch_item_status(
        self,
        course: Course,
        item_url: str,
        page: Page | None = None,
    ) -> tuple[str, str] | None:
        """단일 강의의 (completion, attendance)를 LMS 목록 재스크래핑으로 조회한다.

        재생 성공 판정의 단일 진실 소스. 파싱은 `_fetch_lectures_on`을 그대로 재사용해
        LMS DOM이 바뀌어도 수정 지점이 한 곳으로 유지된다.

        page가 None이면 내부에서 새 page를 만들고 끝나면 닫는다.
        목록에서 해당 강의를 찾지 못하면 None.
        """
        owned = page is None
        if owned:
            page = await self.new_page()
        try:
            detail = await self._fetch_lectures_on(page, course)
        finally:
            if owned:
                await self.close_page(page)

        for week in detail.weeks:
            for lec in week.lectures:
                if lec.full_url == item_url or lec.item_url == item_url:
                    return lec.completion, lec.attendance
        return None

    async def fetch_all_details(
        self,
        courses: list[Course],
        concurrency: int = 3,
        on_complete: Callable[[], None] | None = None,
    ) -> list[CourseDetail | None]:
        """여러 과목의 강의 상세를 병렬로 로드한다."""
        sem = asyncio.Semaphore(concurrency)
        results: list[CourseDetail | None] = [None] * len(courses)
        self._session_restored = False

        async def _fetch_one(idx: int, course: Course):
            async with sem:
                page = await self._context.new_page()
                try:
                    results[idx] = await self._fetch_lectures_on(page, course)
                except Exception as e:
                    tb = traceback.format_exc()
                    msg = f"강의 로딩 실패 ({course.long_name}): {e}\n{tb}"
                    self._log(msg)
                    try:
                        log_dir = Path(__file__).parent.parent.parent / "logs"
                        log_dir.mkdir(exist_ok=True)
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        log_path = log_dir / f"{ts}_scraper_error.log"
                        log_path.write_text(msg, encoding="utf-8")
                    except Exception:
                        print(msg, file=sys.stderr)
                    results[idx] = None
                finally:
                    await page.close()
                    if on_complete:
                        on_complete()

        await asyncio.gather(*[_fetch_one(i, c) for i, c in enumerate(courses)])
        return results

    async def _fetch_lectures_on(self, page: Page, course: Course) -> CourseDetail:
        """지정된 페이지로 과목의 주차별 강의 목록을 스크래핑한다."""
        self._log(f"강의 목록 로딩: {course.long_name}")
        # networkidle로 Canvas JS + iframe 로드까지 대기 (timeout 60초로 여유 확보)
        await page.goto(course.lectures_url, wait_until="networkidle", timeout=60000)

        # 세션 만료 시 재로그인
        if await _needs_login(page):
            async with self._login_lock:
                if not self._session_restored:
                    self._log("세션 만료 감지 — 자동 재로그인 중...")
                    ok = await ensure_logged_in(page, self.username, self.password)
                    if not ok:
                        raise RuntimeError("자동 재로그인 실패. 학번/비밀번호를 확인하세요.")
                    self._log("재로그인 완료")
                    self._session_restored = True
            await page.goto(course.lectures_url, wait_until="networkidle", timeout=60000)

        iframe_el = await page.wait_for_selector("iframe#tool_content", timeout=15000)
        iframe = await iframe_el.content_frame()
        if not iframe:
            raise RuntimeError("iframe을 찾을 수 없습니다.")

        await iframe.wait_for_selector(".xnmb-module-list", timeout=30000)

        root = await iframe.query_selector("#root")
        course_name = await root.get_attribute("data-course_name") or course.long_name
        professors = await root.get_attribute("data-professors") or ""

        expand_btn = await iframe.query_selector(".xnmb-all_fold-btn")
        if expand_btn:
            btn_text = await expand_btn.text_content()
            if btn_text and "펼치기" in btn_text:
                # Canvas 상단 nav가 버튼을 가려 pointer events를 intercept하는 경우가 있어
                # JS로 직접 click() 호출하여 오버레이 무관하게 동작
                await expand_btn.evaluate("el => el.click()")
                await asyncio.sleep(0.5)

        weeks = await self._parse_weeks(iframe)
        return CourseDetail(course=course, course_name=course_name, professors=professors, weeks=weeks)

    async def _parse_weeks(self, iframe: Frame) -> list[Week]:
        module_list = await iframe.query_selector(".xnmb-module-list")
        if not module_list:
            self._log("강의 목록을 찾을 수 없습니다 (.xnmb-module-list). LMS 구조가 변경되었을 수 있습니다.")
            return []

        top_divs = await module_list.query_selector_all(":scope > div")
        weeks = []
        for div in top_divs:
            header = await div.query_selector(".xnmb-module-outer-wrapper")
            if not header:
                continue

            title_el = await header.query_selector(".xnmb-module-title")
            title = (await title_el.text_content()).strip() if title_el else ""

            week_num = len(weeks) + 1
            match = re.search(r"(\d+)주차", title)
            if match:
                week_num = int(match.group(1))

            items = await div.query_selector_all(".xnmb-module_item-outer-wrapper")
            lectures = []
            for item_el in items:
                lecture = await self._parse_item(item_el)
                if lecture:
                    lectures.append(lecture)

            weeks.append(Week(title=title, week_number=week_num, lectures=lectures))
        return weeks

    async def _parse_item(self, el) -> LectureItem | None:
        icon_el = await el.query_selector("i.xnmb-module_item-icon")
        lecture_type = LectureType.OTHER
        if icon_el:
            classes = await icon_el.get_attribute("class") or ""
            for cls_name, lt in _TYPE_CLASS_MAP.items():
                if cls_name in classes.split():
                    lecture_type = lt
                    break

        title_el = await el.query_selector("a.xnmb-module_item-left-title")
        if not title_el:
            title_el = await el.query_selector(".xnmb-module_item-left-title")
            if not title_el:
                return None
            title = (await title_el.text_content() or "").strip()
            item_url = ""
        else:
            title = (await title_el.text_content() or "").strip()
            item_url = await title_el.get_attribute("href") or ""
            if "?" in item_url:
                item_url = item_url.split("?")[0]

        if not title:
            return None

        duration = None
        periods_el = await el.query_selector("[class*='lecture_periods']")
        if periods_el:
            spans = await periods_el.query_selector_all("span")
            for span in reversed(spans):
                text = (await span.text_content() or "").strip()
                if re.match(r"^\d+:\d+$", text):
                    duration = text
                    break

        week_label = ""
        lesson_label = ""
        start_date = None
        end_date = None

        week_span = await el.query_selector("[class*='lesson_periods-week']")
        if week_span:
            week_label = (await week_span.text_content() or "").strip()
        lesson_span = await el.query_selector("[class*='lesson_periods-lesson']")
        if lesson_span:
            lesson_label = (await lesson_span.text_content() or "").strip()

        # 시작/마감 날짜 추출 (예: "3월 10일 오전 00:00")
        unlock_el = await el.query_selector("[class*='lecture_periods-unlock_at'] span")
        if unlock_el:
            start_date = (await unlock_el.text_content() or "").strip() or None
        due_el = await el.query_selector("[class*='lecture_periods-due_at'] span")
        if due_el:
            end_date = (await due_el.text_content() or "").strip() or None

        attendance = "none"
        att_el = await el.query_selector("[class*='attendance_status']")
        if att_el:
            attendance = _parse_attendance_class(await att_el.get_attribute("class") or "")

        completion = "incomplete"
        comp_el = await el.query_selector("[class*='module_item-completed']")
        if comp_el:
            completion = _parse_completion_class(await comp_el.get_attribute("class") or "")

        is_upcoming = False
        dday_el = await el.query_selector(".xncb-component-sub-d_day")
        if dday_el:
            dday_classes = await dday_el.get_attribute("class") or ""
            if "upcoming" in dday_classes:
                is_upcoming = True

        return LectureItem(
            title=title,
            item_url=item_url,
            lecture_type=lecture_type,
            week_label=week_label,
            lesson_label=lesson_label,
            duration=duration,
            attendance=attendance,
            completion=completion,
            is_upcoming=is_upcoming,
            start_date=start_date,
            end_date=end_date,
        )

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()
