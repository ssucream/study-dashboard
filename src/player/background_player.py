"""
백그라운드 재생 모듈.

LMS 강의 페이지를 headless 브라우저로 열고, 영상을 재생하여
LMS가 수강 완료로 인식하도록 실제 재생 시간을 유지한다.

재생 흐름:
1. 강의 페이지 이동 (item_url)
2. 중첩 iframe 탐색 (tool_content → commons.ssu.ac.kr)
3. 이어보기 다이얼로그 자동 처리 (처음부터 재생)
4. 재생 버튼 클릭 후 video 요소의 currentTime / duration 폴링
5. 영상 끝날 때까지 진행 콜백 호출
"""

import asyncio
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from playwright.async_api import Frame, Page

# ── 상수 ─────────────────────────────────────────────────────────
_POLL_INTERVAL = 1.0  # 진행 폴링 주기 (초)
_FRAME_FIND_TIMEOUT = 30  # iframe 탐색 최대 대기 (초)
_PLAY_TIMEOUT = 20  # 재생 버튼/영상 시작 대기 (초)
_END_THRESHOLD = 3  # 영상 끝 판정 여유 (초)
_RESUME_BTN = ".confirm-ok-btn"
_RESTART_BTN = ".confirm-cancel-btn"
_DIALOG_SEL = ".confirm-msg-box"
_PLAY_BTN = ".vc-front-screen-play-btn"
_VIDEO_SEL = "video.vc-vplay-video1"


@dataclass
class PlaybackState:
    current: float = 0.0  # 현재 재생 위치 (초)
    duration: float = 0.0  # 전체 길이 (초)
    ended: bool = False
    error: str | None = None
    cancelled: bool = False  # 사용자 중단(CancelledError) 여부 — error 문자열 비교 대신 이 플래그로 판별할 것


# ── 내부 헬퍼 ────────────────────────────────────────────────────


async def _find_player_frame(page: Page) -> Frame | None:
    """
    tool_content 아래 commons.ssu.ac.kr frame을 찾는다.
    재생 버튼이 있는 초기 플레이어 선택 화면 frame.
    flashErrorPage는 제외한다.
    """
    for _ in range(_FRAME_FIND_TIMEOUT):
        outer = page.frame(name="tool_content")
        if outer:
            for frame in page.frames:
                if (
                    frame.parent_frame == outer
                    and "commons.ssu.ac.kr" in frame.url
                    and "flashErrorPage" not in frame.url
                ):
                    return frame
        await asyncio.sleep(1)
    return None


async def _find_video_frame(page: Page) -> Frame | None:
    """
    실제 video 태그가 있는 frame을 찾는다.
    재생 버튼 클릭 후 page 전체를 재스캔한다.

    commons.ssu.ac.kr에 속한 모든 frame (flashErrorPage 포함) 중
    video 태그가 존재하는 것을 반환한다.
    flashErrorPage 자체가 HTML5 video를 동적으로 생성할 수 있으므로 포함한다.
    """
    for _ in range(_FRAME_FIND_TIMEOUT):
        for frame in page.frames:
            if "commons.ssu.ac.kr" not in frame.url:
                continue
            try:
                count = await frame.evaluate("() => document.querySelectorAll('video').length")
                if count > 0:
                    return frame
            except Exception:
                pass
        await asyncio.sleep(1)
    return None


async def _dismiss_dialog(frame: Frame, restart: bool = True) -> bool:
    """이어보기 다이얼로그가 표시되면 처리한다. 처리했으면 True 반환."""
    try:
        dialog = await frame.query_selector(_DIALOG_SEL)
        if not dialog or not await dialog.is_visible():
            return False
        # 처음부터 재생 (restart=True) 또는 이어보기 (restart=False)
        btn_sel = _RESTART_BTN if restart else _RESUME_BTN
        btn = await frame.query_selector(btn_sel)
        if btn:
            await btn.click()
            return True
    except Exception:
        pass
    return False


async def _click_play(frame: Frame) -> bool:
    """재생 버튼을 클릭한다. 성공 시 True."""
    try:
        btn = await frame.wait_for_selector(_PLAY_BTN, timeout=_PLAY_TIMEOUT * 1000)
        if btn:
            await btn.click()
            return True
    except Exception:
        pass
    return False


async def _get_video_state(frame: Frame) -> dict | None:
    """video 요소의 현재 상태(currentTime, duration, ended, paused)를 반환한다."""
    try:
        return await frame.evaluate(f"""() => {{
            const v = document.querySelector('{_VIDEO_SEL}');
            if (!v) return null;
            return {{
                current: v.currentTime,
                duration: v.duration || 0,
                ended: v.ended,
                paused: v.paused
            }};
        }}""")
    except Exception:
        return None


async def _ensure_playing(frame: Frame):
    """일시정지 상태면 JS로 강제 재생한다."""
    try:
        await frame.evaluate(f"""() => {{
            const v = document.querySelector('{_VIDEO_SEL}');
            if (v && v.paused && !v.ended) v.play();
        }}""")
    except Exception:
        pass


async def _create_fake_webm(duration_sec: float) -> bytes:
    """VP8 WebM 더미 영상 생성 (Chromium H.264 미지원 우회).

    2×2 픽셀 검정 프레임, 1fps, 극소 용량.
    Chromium headless는 H.264를 지원하지 않지만 VP8/WebM은 기본 지원한다.
    commonscdn MP4 요청을 이 영상으로 교체하면 Plan A(video DOM 폴링)가 동작한다.
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "fake.webm")
        dur = str(int(duration_sec) + 2)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=2x2:r=1",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-t",
            dur,
            "-c:v",
            "libvpx",
            "-b:v",
            "1k",
            "-c:a",
            "libopus",
            "-b:a",
            "8k",
            "-map",
            "0:v",
            "-map",
            "1:a",
            output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("ffmpeg 더미 영상 생성 실패")
        with open(output_path, "rb") as f:
            return f.read()


# ── 진도 API 직접 호출 (Plan B) ──────────────────────────────────


def _parse_player_url(player_url: str) -> dict:
    """
    commons.ssu.ac.kr/em/ URL에서 재생 정보를 파싱한다.

    반환:
        {
            "content_id": str,
            "duration": float,       # endat 파라미터 (초)
            "progress_url": str,     # TargetUrl 디코딩값
        }
    """
    parsed = urlparse(player_url)
    qs = parse_qs(parsed.query)

    raw_endat = float(qs.get("endat", ["0"])[0])
    # LMS가 duration 미확정 강의에 sentinel 값(-8888 등 음수)을 사용하는 경우 0으로 정규화
    duration = max(raw_endat, 0.0)
    target_url = unquote(qs.get("TargetUrl", [""])[0])

    # content_id는 path의 마지막 세그먼트
    content_id = parsed.path.rstrip("/").split("/")[-1]

    return {
        "content_id": content_id,
        "duration": duration,
        "progress_url": target_url,
    }


async def _call_progress_jsonp(frame: Frame, report_url: str, callback: str) -> str:
    """
    commons 프레임 내부에서 JSONP 스크립트 태그를 주입해 진도 API를 호출한다.

    실제 플레이어(uni-player.min.js)와 동일하게 commons.ssu.ac.kr origin에서
    canvas.ssu.ac.kr progress 엔드포인트를 호출함으로써 ErrAlreadyInView를 우회한다.
    """
    result = await frame.evaluate(
        """
        (args) => new Promise((resolve) => {
            var url = args[0];
            var cbName = args[1];
            window[cbName] = function(data) {
                delete window[cbName];
                resolve(JSON.stringify(data));
            };
            var s = document.createElement('script');
            s.src = url;
            s.onerror = function() {
                delete window[cbName];
                resolve(JSON.stringify({error: 'script_error'}));
            };
            document.head.appendChild(s);
            setTimeout(function() {
                delete window[cbName];
                resolve(JSON.stringify({error: 'timeout'}));
            }, 10000);
        })
    """,
        [report_url, callback],
    )
    return result


async def _report_completion(
    page: Page,
    player_url: str,
    duration: float,
    log: Callable,
    commons_frame: Frame | None = None,
    use_page_eval: bool = False,
):
    """
    Plan A/B 완료 후 progress API에 100% 진도를 한 번 직접 보고한다.

    플레이어 JS(uni-player-event.js)가 가짜 WebM 재생 중 progress API를 호출하지
    않는 경우를 대비한 안전망. Plan A가 성공하더라도 항상 호출한다.

    ErrAlreadyInView 처리:
    - use_page_eval=True (Plan A): page.evaluate fetch로 canvas.ssu.ac.kr 동일 오리진 호출.
      Plan A에서는 sl=1 세션이 활성 중이므로 JSONP 대신 이 방식을 사용.
    - commons_frame 있음 (Plan B): JSONP 방식으로 sl=0 세션에서 호출 (ErrAlreadyInView 우회).
    - 둘 다 없거나 실패 시: page.request.get으로 폴백.
    """
    import time

    info = _parse_player_url(player_url)
    progress_url = info["progress_url"]
    if not progress_url:
        log("  [완료 보고] TargetUrl 없음 — 건너뜀")
        return

    if duration <= 0:
        duration = info["duration"]
    if duration <= 0:
        log("  [완료 보고] duration 불명 — 건너뜀")
        return

    total_page = 15
    sep = "&" if "?" in progress_url else "?"

    def _build_url() -> tuple[str, str]:
        ts = int(time.time() * 1000)
        cb = f"jQuery111_{ts}"
        url = (
            f"{progress_url}{sep}"
            f"callback={cb}"
            f"&state=3"
            f"&duration={duration}"
            f"&currentTime={duration:.2f}"
            f"&cumulativeTime={duration:.2f}"
            f"&page={total_page}"
            f"&totalpage={total_page}"
            f"&cumulativePage={total_page}"
            f"&_={ts}"
        )
        return url, cb

    for attempt in range(3):
        if attempt > 0:
            log(f"  [완료 보고] 재시도 {attempt + 1}/3 (2초 대기 후)")
            await asyncio.sleep(2)

        log(f"  [완료 보고] 100% 진도 직접 전송 (duration={duration:.1f}s)")

        # Plan A: page.evaluate fetch (canvas.ssu.ac.kr 동일 오리진 — sl=1 세션 중에도 동작)
        if use_page_eval:
            report_url, _ = _build_url()
            try:
                result = await page.evaluate(f"""
                    async () => {{
                        try {{
                            const resp = await fetch({json.dumps(report_url)});
                            return {{s: resp.status, b: (await resp.text()).slice(0, 300)}};
                        }} catch(e) {{
                            return {{s: -1, b: e.message}};
                        }}
                    }}
                """)
                status = result.get("s")
                body = result.get("b", "")
                log(f"  [완료 보고] page ctx fetch: {status}  body={body!r}")
                if status == 200 and '"result":true' in body:
                    return
                log(f"  [완료 보고] page ctx fetch 실패 ({status}) — page.request.get으로 폴백")
            except Exception as e:
                log(f"  [완료 보고] page ctx fetch 오류: {e}")

        # Plan B: commons_frame JSONP (sl=0 세션 — ErrAlreadyInView 우회)
        elif commons_frame:
            report_url, callback = _build_url()
            try:
                body = await _call_progress_jsonp(commons_frame, report_url, callback)
                log(f"  [완료 보고] JSONP 응답: {body[:200]!r}")
                if '"result":true' in body:
                    return
                log("  [완료 보고] JSONP 결과 false — page.request.get으로 폴백")
            except Exception as e:
                log(f"  [완료 보고] JSONP 실패 ({e}) — page.request.get으로 폴백")

        # 폴백: page.request.get
        report_url_fb, _ = _build_url()
        try:
            response = await page.request.get(
                report_url_fb,
                headers={"Referer": "https://commons.ssu.ac.kr/"},
            )
            body = await response.text()
            log(f"  [완료 보고] request.get 응답: {response.status}  body={body[:200]!r}")
            if '"result":true' in body:
                return
        except Exception as e:
            log(f"  [완료 보고] request.get 실패: {e}")

    log("  [완료 보고] 3회 시도 모두 실패 — 출석이 인정되지 않았을 수 있습니다")


async def _fetch_learningx_duration(page: Page, learningx_url: str, log: Callable) -> float:
    """
    learningx URL에서 item_content_data.duration을 조회해 반환한다.
    실패 시 0.0 반환.
    """
    m = re.search(r"/lecture_attendance/items/view/(\d+)", learningx_url)
    cm = re.search(r"/courses/(\d+)/", page.url)
    if not m or not cm:
        return 0.0

    item_id = m.group(1)
    course_id = cm.group(1)
    api_url = f"https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/attendance_items/{item_id}"

    try:
        resp = await page.request.get(api_url)
        if resp.status != 200:
            return 0.0
        data = json.loads(await resp.text())
        return float((data.get("item_content_data") or {}).get("duration") or 0)
    except Exception:
        return 0.0


async def _play_via_learningx_api(
    page: Page,
    learningx_url: str,
    on_progress: Callable[[PlaybackState], None] | None,
    log: Callable,
    fallback_duration: float = 0.0,
) -> PlaybackState:
    """
    learningx 플레이어 전용 Plan B.

    learningx /api/v1/courses/{course_id}/attendance_items/{item_id} 에서
    viewer_url을 가져오면 commons TargetUrl이 포함되어 있어,
    기존 _play_via_progress_api를 그대로 재사용할 수 있다.

    learningx_url 예시:
      https://canvas.ssu.ac.kr/learningx/lti/lecture_attendance/items/view/764082
    """
    import re as _re

    state = PlaybackState()

    # URL에서 item_id, course_id 추출
    # tool_content frame URL이 아닌 learningx API를 직접 호출해야 하므로
    # page 컨텍스트(canvas.ssu.ac.kr)에서 fetch — 쿠키 자동 포함
    m = _re.search(r"/lecture_attendance/items/view/(\d+)", learningx_url)
    if not m:
        log(f"  [LX] item_id 파싱 실패: {learningx_url}")
        state.error = "learningx item_id를 파싱하지 못했습니다."
        return state

    item_id = m.group(1)

    # course_id는 페이지 URL에서 추출
    cm = _re.search(r"/courses/(\d+)/", page.url)
    if not cm:
        log(f"  [LX] course_id 파싱 실패: {page.url}")
        state.error = "learningx course_id를 파싱하지 못했습니다."
        return state

    course_id = cm.group(1)
    api_url = f"https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/attendance_items/{item_id}"
    log(f"  [LX] learningx item API 호출: {api_url}")

    # page.evaluate(fetch) 방식: canvas.ssu.ac.kr 쿠키 포함, LTI 세션 쿠키는 미포함 가능
    # 401 시 page.request 방식(Playwright 브라우저 컨텍스트 전체 쿠키 포함)으로 재시도
    status = -1
    body = ""
    try:
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const resp = await fetch({json.dumps(api_url)});
                    return {{s: resp.status, b: await resp.text()}};
                }} catch(e) {{
                    return {{s: -1, b: e.message}};
                }}
            }}
        """)
        status = result.get("s")
        body = result.get("b", "")
        log(f"  [LX] API 응답(fetch): {status}")
    except Exception as e:
        log(f"  [LX] API 호출 실패(fetch): {e}")

    if status != 200:
        log(f"  [LX] fetch 방식 실패({status}) — page.request 방식으로 재시도")
        try:
            resp = await page.request.get(api_url)
            status = resp.status
            body = await resp.text()
            log(f"  [LX] API 응답(request): {status}")
        except Exception as e:
            log(f"  [LX] API 호출 실패(request): {e}")
            state.error = f"learningx API 호출 실패: {e}"
            return state

    if status != 200:
        state.error = f"learningx API 오류: {status}"
        return state

    try:
        data = json.loads(body)
    except Exception:
        log(f"  [LX] JSON 파싱 실패: {body[:200]!r}")
        state.error = "learningx API 응답 파싱 실패"
        return state

    viewer_url = data.get("viewer_url", "")
    if not viewer_url:
        log("  [LX] viewer_url 없음")
        state.error = "learningx viewer_url 없음"
        return state

    duration = float(data.get("item_content_data", {}).get("duration", 0) or 0)
    log(f"  [LX] viewer_url={viewer_url}")
    log(f"  [LX] duration={duration:.1f}s — Plan B로 전환")

    # viewer_url의 endat/startat이 이전 진도(예: 330.00)로 고정되어 있는 경우
    # 실제 duration으로 교체해서 commons가 전체 영상을 기준으로 동작하도록 한다.
    if duration > 0 and "endat=" in viewer_url:
        parsed_vu = urlparse(viewer_url)
        qs_vu = parse_qs(parsed_vu.query, keep_blank_values=True)
        old_endat = float(qs_vu.get("endat", ["0"])[0])
        if abs(old_endat - duration) > 10:
            import re as _re

            viewer_url = _re.sub(r"endat=[^&]+", f"endat={duration:.2f}", viewer_url)
            viewer_url = _re.sub(r"startat=[^&]+", "startat=0.00", viewer_url)
            log(f"  [LX] endat 교정: {old_endat:.2f}s → {duration:.2f}s")

    return await _play_via_progress_api(
        page,
        viewer_url,
        on_progress,
        log,
        fallback_duration=duration if duration > 0 else fallback_duration,
    )


async def _play_via_progress_api(
    page: Page,
    player_url: str,
    on_progress: Callable[[PlaybackState], None] | None,
    log: Callable,
    fallback_duration: float = 0.0,
    existing_commons_frame: "Frame | None" = None,
) -> PlaybackState:
    """
    headless에서 플레이어 로드에 실패할 때 사용하는 Plan B.

    진도 API(TargetUrl)를 주기적으로 호출해서 LMS가 수강 완료로 인식하도록 한다.

    existing_commons_frame:
        Plan A에서 이미 로드된 sl=1 commons 프레임. 제공 시 sl=0 재로드를 건너뛰고
        기존 프레임에서 JSONP를 주입한다. movie 콘텐츠에서 sl=0 전환 후에도
        ErrAlreadyInView가 발생하는 문제를 근본적으로 해결한다.

    ErrAlreadyInView 우회 전략:
    - sl=1 파라미터로 commons.ssu.ac.kr에 뷰 세션이 등록된 상태에서
      canvas.ssu.ac.kr 컨텍스트에서 직접 progress API를 호출하면 ErrAlreadyInView가 반환됨.
    - commons 프레임(flashErrorPage.html)이 아직 살아있을 때,
      그 프레임 내부에서 JSONP 스크립트 태그를 주입해 호출하면
      실제 플레이어와 동일한 commons.ssu.ac.kr origin으로 요청이 전송되어 우회 가능.
    - commons 프레임이 없으면 대시보드로 이동 후 page.request.get으로 폴백.
    """
    state = PlaybackState()
    info = _parse_player_url(player_url)
    duration = info["duration"]
    progress_url = info["progress_url"]

    if not progress_url:
        log("  [API] TargetUrl 파싱 실패 — 재생 불가")
        state.error = "진도 API URL을 파싱하지 못했습니다."
        return state

    # fallback_duration이 endat보다 유의미하게 크면 실제 강의 길이로 교체한다.
    # endat은 이전 시청 위치(이어보기 포인트)일 수 있어서 실제 duration보다 훨씬 작을 수 있다.
    if fallback_duration > duration + 10:
        log(f"  [API] endat({duration:.1f}s) < fallback_duration({fallback_duration:.1f}s) — fallback duration 사용")
        duration = fallback_duration

    if duration <= 0:
        if fallback_duration > 0:
            log(f"  [API] endat 미확정(endat=0 또는 sentinel 값) — fallback duration 사용: {fallback_duration:.1f}s")
            duration = fallback_duration
        else:
            # progress_url에서 course_id / component_id를 추출해 attendance_items API로 duration 조회
            # progress_url 형식: .../courses/{course_id}/sections/0/components/{component_id}/progress
            _m = re.search(r"/courses/(\d+)/sections/\d+/components/(\d+)/progress", progress_url)
            if _m:
                _course_id, _component_id = _m.group(1), _m.group(2)
                _items_url = (
                    f"https://canvas.ssu.ac.kr/learningx/api/v1/courses/{_course_id}/attendance_items/{_component_id}"
                )
                log(f"  [API] attendance_items API로 duration 조회 중: {_items_url}")
                try:
                    _resp = await page.request.get(_items_url)
                    _body = await _resp.text()
                    log(f"  [API] attendance_items 응답: status={_resp.status} body={_body[:200]!r}")
                    _data = json.loads(_body)
                    _api_duration = float((_data.get("item_content_data") or {}).get("duration") or 0)
                    if _api_duration > 0:
                        log(f"  [API] attendance_items duration={_api_duration:.1f}s — 사용")
                        duration = _api_duration
                    else:
                        log("  [API] attendance_items duration 값이 0 또는 없음")
                except Exception as _e:
                    log(f"  [API] attendance_items 조회 실패: {_e}")

            if duration <= 0:
                log("  [API] duration 파싱 실패 — endat 미확정이고 fallback duration도 없음")
                state.error = "영상 길이를 알 수 없습니다."
                return state

    # ErrAlreadyInView 대응 전략:
    # sl=0으로 재로드하면 서버 측 sl=1 세션이 닫히지 않아 계속 ErrAlreadyInView 발생.
    # sl=1으로 재로드하면 현재 세션이 서버에 활성 뷰어로 재등록되어 진도 API 수락됨.
    #
    # - existing_commons_frame 제공 시: page.frames에서 여전히 살아있는 경우 재사용.
    #   살아있지 않으면 sl=1 재로드로 폴백.
    # - 미제공 시: sl=1로 commons를 재로드해 현재 세션을 서버에 재등록 후 JSONP 호출.
    commons_frame: Frame | None = None
    _flash_block_handler: list = []  # [handler] — 루프 종료 후 해제용

    if existing_commons_frame is not None:
        # 전달된 frame이 아직 page.frames에 살아있고 flashErrorPage로 이동하지 않았는지 확인.
        # flashErrorPage로 이동한 frame은 sl=1 세션이 무효화된 상태이므로 재사용 불가.
        live_frames = page.frames
        # flashErrorPage를 route.fulfill()로 빈 HTML 대체 시: frame URL은 flashErrorPage.html로
        # 바뀌지만 서버 측 sl=1 세션은 살아있음(서버에 요청이 도달하지 않으므로).
        # → flashErrorPage URL이어도 frame이 live하면 재사용 허용.
        if existing_commons_frame in live_frames:
            commons_frame = existing_commons_frame
            if "flashErrorPage" in existing_commons_frame.url:
                log("  [API] 기존 commons frame이 flashErrorPage(fulfill)로 전환됨 — 세션 유지로 재사용")
            else:
                log("  [API] 기존 sl=1 commons frame 재사용 — 재로드 건너뜀")
        else:
            log("  [API] 전달된 commons frame이 이미 detach됨 — sl=1 재로드로 폴백")

    if commons_frame is None:
        # ErrAlreadyInView 근본 원인: 초기 page.goto(lecture_url) 시 commons iframe이
        # viewer_url(sl=1)로 자동 로드되어 서버에 뷰 세션이 등록됨.
        # 이 세션이 살아있는 한 이후 모든 progress API 호출이 ErrAlreadyInView로 거부됨.
        # → sl=0 URL로 요청해 서버가 세션을 닫도록 유도한 뒤 sl=1 재로드.
        # 주의: Plan A 실패 후 commons frame은 이미 flashErrorPage로 이동해 sl=1이 URL에 없음.
        #       player_url(sl=1)을 직접 변환해 sl=0 요청을 보낸다.
        if "sl=1" in player_url:
            _sl0_url = player_url.replace("sl=1", "sl=0")
            try:
                log(f"  [API] 기존 sl=1 세션 종료 시도 (sl=0 GET): {_sl0_url[:80]}...")
                await page.request.get(_sl0_url, headers={"Referer": "https://canvas.ssu.ac.kr/"})
                await asyncio.sleep(2)
                log("  [API] sl=0 요청 완료")
            except Exception as _e:
                log(f"  [API] sl=0 요청 실패 ({_e}) — 계속 진행")

        try:
            log(f"  [API] sl=1로 commons 재로드 (세션 재확립): {player_url[:80]}...")

            # ErrAlreadyInView 근본 원인 해결 전략:
            # 1. about:blank 이동: 기존 canvas iframe 세션을 DOM에서 제거해 서버 측 세션 충돌 최소화
            # 2. uni-player*.js 차단: 플레이어 JS가 자체 initUniPlayerEventListener를 실행해
            #    서버에 독립 뷰어 세션을 등록하면 우리 JSONP가 ErrAlreadyInView를 받음.
            #    JS를 차단하면 JSONP가 유일한 세션 소유자가 됨.
            # 3. flashErrorPage 차단을 진도 루프 전체로 연장: 루프 중 플레이어가
            #    flashErrorPage로 이동하면 sl=1 세션이 무효화됨.
            async def _block_flash_error_page(route):
                # abort() 대신 빈 HTML: abort()는 frame을 chrome-error:// broken 상태로 만들어
                # JSONP <script> cross-origin 로드를 차단함.
                await route.fulfill(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body=b"<html><body></body></html>",
                )

            async def _block_player_js(route):
                await route.abort()

            await page.route("**/flashErrorPage.html", _block_flash_error_page)
            await page.route("**/uni-player*.js*", _block_player_js)
            _flash_block_handler.append(_block_flash_error_page)  # 루프 후 해제
            try:
                # 기존 iframe 세션 DOM 정리: canvas 컨텍스트 해제
                try:
                    await page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
                    await asyncio.sleep(1)
                except Exception:
                    pass
                await page.goto(player_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(5)  # 세션 안정화 대기 (3 → 5초)
            finally:
                # 플레이어 JS 차단만 해제 — flashErrorPage는 루프 전체에서 유지
                await page.unroute("**/uni-player*.js*", _block_player_js)
            # sl=1로 로드된 commons frame 탐색 (flashErrorPage 제외)
            for f in page.frames:
                if "commons.ssu.ac.kr" in f.url and "flashErrorPage" not in f.url:
                    commons_frame = f
                    break
            log(f"  [API] commons frame({'발견' if commons_frame else '없음'})")
        except Exception as e:
            log(f"  [API] commons 재로드 실패 ({e}) — page.request.get으로 폴백")

    log("  [API] 진도 API 방식으로 재생 시뮬레이션")
    log(f"  [API] duration={duration:.1f}s  progress_url={progress_url}")

    state.duration = duration
    current = 0.0
    report_interval = 30.0  # 30초마다 진도 보고
    next_report = report_interval

    # 총 페이지 수는 실제 요청에서 totalpage=15로 고정 (LMS 플레이어 기본값)
    total_page = 15

    while current < duration:
        await asyncio.sleep(_POLL_INTERVAL)
        current = min(current + _POLL_INTERVAL, duration)
        state.current = current

        if on_progress:
            on_progress(state)

        # 30초마다 진도 API 호출
        if current >= next_report or current >= duration:
            try:
                import time

                ts = int(time.time() * 1000)
                callback = f"jQuery111_{ts}"

                cumulative_page = total_page if current >= duration else int(current / duration * total_page)
                page_num = min(cumulative_page, total_page)

                sep = "&" if "?" in progress_url else "?"
                report_target = (
                    f"{progress_url}{sep}"
                    f"callback={callback}"
                    f"&state=3"
                    f"&duration={duration}"
                    f"&currentTime={current:.2f}"
                    f"&cumulativeTime={current:.2f}"
                    f"&page={page_num}"
                    f"&totalpage={total_page}"
                    f"&cumulativePage={cumulative_page}"
                    f"&_={ts}"
                )
                log(f"  [API] 진도 보고: {int(current)}s/{int(duration)}s")

                reported = False
                # 1. page.evaluate fetch: canvas.ssu.ac.kr 동일 오리진으로 호출해
                #    ErrAlreadyInView를 우회한다 (readystream 등 JSONP가 실패하는 콘텐츠 대응).
                try:
                    eval_result = await page.evaluate(f"""
                        async () => {{
                            try {{
                                const resp = await fetch({json.dumps(report_target)});
                                return {{s: resp.status, b: (await resp.text()).slice(0, 300)}};
                            }} catch(e) {{
                                return {{s: -1, b: e.message}};
                            }}
                        }}
                    """)
                    eval_status = eval_result.get("s")
                    eval_body = eval_result.get("b", "")
                    log(f"  [API] 응답 (page ctx): {eval_status}  body={eval_body[:200]!r}")
                    if eval_status == 200 and '"result":true' in eval_body:
                        reported = True
                except Exception as pe:
                    log(f"  [API] page ctx fetch 실패 ({pe}) — JSONP/fallback으로 폴백")

                # 2. JSONP (commons_frame)
                if not reported and commons_frame:
                    try:
                        body = await _call_progress_jsonp(commons_frame, report_target, callback)
                        log(f"  [API] 응답 (JSONP): {body[:200]!r}")
                        reported = True
                    except Exception as je:
                        log(f"  [API] JSONP 실패 ({je}) — page.request.get으로 폴백")
                        commons_frame = None

                # 3. page.request.get 폴백
                if not reported:
                    response = await page.request.get(
                        report_target,
                        headers={"Referer": "https://commons.ssu.ac.kr/"},
                    )
                    body = await response.text()
                    log(f"  [API] 응답 (fallback): {response.status}  body={body[:200]!r}")

                next_report = current + report_interval
            except Exception as e:
                log(f"  [API] 진도 보고 실패: {e} — 다음 폴링에서 재시도")

    state.ended = True
    if on_progress:
        on_progress(state)

    # 재생 루프 종료 후 100% 완료 보고 — commons_frame 재사용으로 ErrAlreadyInView 방지
    await _report_completion(page, player_url, state.duration, log, commons_frame)

    # flashErrorPage 차단 해제 (루프 전체 동안 유지했던 route 정리)
    if _flash_block_handler:
        try:
            await page.unroute("**/flashErrorPage.html", _flash_block_handler[0])
        except Exception:
            pass

    return state


# ── 공개 API ─────────────────────────────────────────────────────


async def _debug_page_state(page: Page, frame: Frame | None, log: Callable):
    """현재 페이지/프레임 상태를 상세 출력한다."""
    log(f"  [현재 URL] {page.url}")
    log(f"  [전체 프레임 수] {len(page.frames)}")
    for i, f in enumerate(page.frames):
        parent_name = f.parent_frame.name if f.parent_frame else "root"
        log(f"    frame[{i}] name={f.name!r}  parent={parent_name!r}  url={f.url}")

    # 모든 commons.ssu.ac.kr frame에 대해 video 조회
    log("  [commons frame별 video 조회]")
    for i, f in enumerate(page.frames):
        if "commons.ssu.ac.kr" not in f.url:
            continue
        try:
            all_videos = await f.evaluate("""() => {
                return Array.from(document.querySelectorAll('video')).map(v => ({
                    class: v.className,
                    src: v.src || v.currentSrc || '(없음)',
                    readyState: v.readyState,
                    duration: v.duration,
                    paused: v.paused,
                    error: v.error ? v.error.code : null
                }));
            }""")
            body_html = await f.evaluate("() => document.body ? document.body.innerHTML.slice(0, 500) : '(body 없음)'")
            log(f"    frame[{i}] url={f.url}")
            log(f"      video 수={len(all_videos)}")
            for j, v in enumerate(all_videos):
                log(
                    f"      video[{j}] class={v['class']!r}  src={v['src'][:100]!r}  "
                    f"readyState={v['readyState']}  duration={v['duration']}  "
                    f"paused={v['paused']}  error={v['error']}"
                )
            log(f"      body(첫 500자)={body_html!r}")
        except Exception as e:
            log(f"    frame[{i}] 조회 오류: {e}")

    if frame is None:
        log("  [지정 video frame] 없음")
        return

    log(f"  [지정 video frame] url={frame.url}")

    # 재생 버튼 존재 여부
    try:
        play_btn = await frame.query_selector(_PLAY_BTN)
        log(f"  [재생 버튼] {'있음' if play_btn else '없음'}")
    except Exception as e:
        log(f"  [재생 버튼 조회 오류] {e}")

    # 이어보기 다이얼로그 존재 여부
    try:
        dialog = await frame.query_selector(_DIALOG_SEL)
        visible = await dialog.is_visible() if dialog else False
        log(f"  [이어보기 다이얼로그] {'표시 중' if visible else ('DOM 있음(숨김)' if dialog else '없음')}")
    except Exception as e:
        log(f"  [다이얼로그 조회 오류] {e}")


async def play_lecture(
    page: Page,
    lecture_url: str,
    on_progress: Callable[[PlaybackState], None] | None = None,
    debug: bool = False,
    fallback_duration: float = 0.0,
    log_fn: Callable | None = None,
) -> PlaybackState:
    """
    강의 URL을 headless 브라우저로 재생한다.

    Args:
        page:         CourseScraper가 관리하는 Playwright Page.
        lecture_url:  LectureItem.full_url
        on_progress:  재생 진행 시 주기적으로 호출되는 콜백. PlaybackState 전달.
        debug:        True이면 단계별 진단 로그를 출력한다.
        log_fn:       debug 출력에 사용할 로그 함수. 미지정 시 print 사용.

    Returns:
        최종 PlaybackState.
    """
    log = log_fn if log_fn else (print if debug else (lambda *a, **k: None))
    state = PlaybackState()

    # 0. H.264 우회: VP8 WebM 더미 영상으로 commonscdn MP4 인터셉트
    # Chromium headless(ARM64 포함)는 H.264 미지원 → flashErrorPage.html 로드 → Plan A 실패
    # VP8 WebM을 대신 제공하면 Chromium이 정상 재생 → Plan A 동작 → LTI 세션 내에서 progress 보고
    #
    # route 핸들러는 MP4 요청이 실제로 왔을 때 그 시점의 fallback_duration으로 lazy 생성한다.
    # 이렇게 하면 page.goto() + commons 로드 후 sniff/meta에서 올바른 duration을 확보한 뒤
    # 실제 MP4 요청 시점에 올바른 길이의 webm을 생성할 수 있다.
    _using_fake_video = False
    _fake_video_cache: list[bytes] = []  # 생성된 webm bytes 캐시 (재요청 대비)
    # fallback_duration을 mutable container로 공유:
    # _serve_fake 클로저(play_lecture 스코프)와 _play_lecture_inner의 교정 로직이
    # 같은 객체를 참조하도록 list[float]로 감쌈. [0]이 현재 duration.
    _shared_duration: list[float] = [fallback_duration]

    # fallback_duration 유무와 무관하게 항상 fake video route를 등록한다.
    # readystream(UPF) 컨텐츠는 fallback_duration=0으로 호출되지만,
    # commons 플레이어가 preloader.mp4로 H.264 코덱 체크를 수행하고
    # 실패 시 즉시 flashErrorPage로 이동한다.
    # attendance_items 스니핑(_sniffed_duration)은 preloader.mp4 요청보다 먼저 완료되므로
    # 여기서 항상 route를 등록해 두면 실제 요청 시 올바른 duration으로 fake WebM을 생성할 수 있다.
    log(f"[0] H.264 우회: lazy fake webm 등록 (초기 duration={fallback_duration:.0f}s, 실제 요청 시 생성)")
    try:

        async def _serve_fake(route, request):
            # _sniffed_duration 우선: attendance_items 응답 스니핑으로 채워지며
            # preloader.mp4 요청보다 먼저 완료된다.
            # _shared_duration은 [2.5] 단계에서 meta/sniff 값으로 교정된다.
            dur = _sniffed_duration[0] if _sniffed_duration else _shared_duration[0]
            if dur <= 0:
                dur = 300  # 최소 5분 fallback (duration 미확정 시)
            if not _fake_video_cache:
                log(f"[0] fake webm 생성 중 (duration={dur:.0f}s)...")
                try:
                    data = await _create_fake_webm(dur)
                    _fake_video_cache.append(data)
                    log(f"[0] fake webm 생성 완료 ({len(data):,} bytes)")
                except Exception as e:
                    log(f"[0] fake webm 생성 실패 ({e}) — 원본으로 폴백")
                    await route.continue_()
                    return
            await route.fulfill(
                status=200,
                headers={"Content-Type": "video/webm"},
                body=_fake_video_cache[0],
            )

        await page.route("**/*.mp4", _serve_fake)
        # canPlayType / isTypeSupported 오버라이드:
        # Chromium은 H.264 미지원 → canPlayType("video/mp4; codecs=avc1") = ""
        # 플레이어가 이 값을 보고 MP4 요청 없이 바로 flashErrorPage로 분기.
        # init script로 'probably'를 반환하게 속이면 MP4를 실제로 요청하고,
        # 그 요청을 위 route가 VP8 WebM으로 대체한다.
        await page.add_init_script("""
            (function() {
                if (window.MediaSource && MediaSource.isTypeSupported) {
                    var _origMSE = MediaSource.isTypeSupported.bind(MediaSource);
                    MediaSource.isTypeSupported = function(type) {
                        if (type && (type.indexOf('avc') !== -1 || type.indexOf('mp4') !== -1)) return true;
                        return _origMSE(type);
                    };
                }
                var _origCPT = HTMLVideoElement.prototype.canPlayType;
                HTMLVideoElement.prototype.canPlayType = function(type) {
                    if (type && (type.indexOf('mp4') !== -1 || type.indexOf('avc') !== -1 || type.indexOf('h264') !== -1)) return 'probably';
                    return _origCPT.call(this, type);
                };
            })();
        """)
        _using_fake_video = True
        log("[0] MP4 인터셉트 (*.mp4 전체) + canPlayType 오버라이드 등록 완료")
    except Exception as e:
        log(f"[0] route 등록 실패 ({e}) — 원본 스트림으로 계속")

    # 1. 강의 페이지로 이동
    log(f"[1] 강의 페이지 이동: {lecture_url}")

    # 네트워크 요청/응답 스니핑 (commons.ssu.ac.kr + canvas learningx 전체)
    # page 객체가 재사용되므로 리스너는 반드시 finally에서 제거해야 누적 방지

    # attendance_items 응답에서 duration을 미리 추출 (debug 여부 무관하게 항상 동작)
    # page.request.get()은 learningx API에 401을 반환하므로,
    # 브라우저가 자동으로 보내는 요청의 응답을 sniff해서 fallback_duration을 채운다.
    _sniffed_duration: list[float] = []  # mutable container (리스너 클로저에서 append)

    async def _sniff_attendance_duration(response):
        # attendance_items API 또는 lecture_attendance LTI POST 응답에서 duration 추출
        # mp4 콘텐츠는 attendance_items를 직접 호출하지 않고 lecture_attendance POST로만 응답이 옴
        if "attendance_items" not in response.url and "lecture_attendance/items/view" not in response.url:
            return
        if response.status != 200:
            return
        try:
            data = json.loads(await response.text())
            d = float((data.get("item_content_data") or {}).get("duration") or 0)
            if d > 0 and not _sniffed_duration:
                _sniffed_duration.append(d)
                log(f"  [SNIFF] duration={d:.1f}s 캡처 ({response.url.split('/')[-1]})")
        except Exception:
            pass

    page.on("response", _sniff_attendance_duration)

    # commons /em/ URL의 endat가 이전 진도로 고정된 경우 실제 duration으로 교정
    # attendance_items sniff로 duration을 먼저 얻고, 그 값으로 endat를 교체한다.
    async def _fix_commons_endat(route, request):
        url = request.url
        if "commons.ssu.ac.kr/em/" not in url or "endat=" not in url:
            await route.continue_()
            return
        if not _sniffed_duration:
            await route.continue_()
            return
        real_dur = _sniffed_duration[0]
        parsed_u = urlparse(url)
        qs_u = parse_qs(parsed_u.query, keep_blank_values=True)
        old_endat = float(qs_u.get("endat", ["0"])[0])
        if abs(old_endat - real_dur) > 10:
            fixed = re.sub(r"endat=[^&]+", f"endat={real_dur:.2f}", url)
            fixed = re.sub(r"startat=[^&]+", "startat=0.00", fixed)
            log(f"  [ROUTE] commons endat 교정: {old_endat:.2f}s → {real_dur:.2f}s")
            await route.fulfill(status=302, headers={"Location": fixed})
        else:
            await route.continue_()

    try:
        await page.route("**/commons.ssu.ac.kr/em/**", _fix_commons_endat)
    except Exception as e:
        log(f"[0] commons endat route 등록 실패: {e}")

    # flashErrorPage를 초기부터 차단한다.
    # commons 플레이어가 H.264 재생 실패 시 flashErrorPage로 이동하면 sl=1 세션이 무효화되어
    # Plan B에서 ErrAlreadyInView가 발생한다.
    # page.goto(lecture_url) 이전에 차단하면 commons iframe이 원래 URL을 유지하므로
    # Plan B의 existing_commons_frame 재사용 경로가 동작하고 JSONP 진도 보고가 성공한다.
    _block_flash_global_handler: list = []

    async def _block_flash_global(route):
        # abort() 대신 빈 HTML fulfill: abort()는 frame을 chrome-error:// 상태로 만들어
        # JSONP <script> 로드를 차단한다. 빈 페이지로 대체하면 frame이 정상 상태 유지.
        await route.fulfill(
            status=200,
            headers={"Content-Type": "text/html"},
            body=b"<html><body></body></html>",
        )

    try:
        await page.route("**/flashErrorPage.html", _block_flash_global)
        _block_flash_global_handler.append(_block_flash_global)
        log("[0] flashErrorPage 초기 차단 등록 완료")
    except Exception as e:
        log(f"[0] flashErrorPage 차단 등록 실패: {e}")

    _on_request = None
    _on_response = None
    if debug:

        def _on_request(request):
            url = request.url
            if "google-analytics" in url or "gtm" in url:
                return
            if "commons.ssu.ac.kr" in url or "learningx" in url:
                log(f"  [SNIFF→REQ] {request.method} {url}")
                if request.post_data:
                    log(f"  [SNIFF→REQ] body={request.post_data!r}")

        _FULL_BODY_KEYWORDS = (
            "attendance_items",
            "content.php",
            "chapter.xml",
            "progress",
            "lessons",
            "lecture_attendance",
        )

        async def _on_response(response):
            url = response.url
            if "google-analytics" in url or "gtm" in url:
                return
            if "commons.ssu.ac.kr" in url or "learningx" in url:
                try:
                    body = await response.text()
                except Exception:
                    body = "(읽기 실패)"
                headers = dict(response.headers)
                set_cookie = headers.get("set-cookie", "")
                log(f"  [SNIFF←RES] {response.status} {url}")
                if set_cookie:
                    log(f"  [SNIFF←RES] set-cookie={set_cookie}")
                # 중요 API는 전체 body 출력, 나머지는 500자 제한
                # 4xx 응답에서 body가 비어있어도 명시적으로 로깅 (원인 진단용)
                if response.status >= 400 and any(kw in url for kw in _FULL_BODY_KEYWORDS):
                    log(f"  [SNIFF←RES] body(4xx)={body!r}")
                elif body:
                    if any(kw in url for kw in _FULL_BODY_KEYWORDS) or len(body) < 500:
                        log(f"  [SNIFF←RES] body={body!r}")

        page.on("request", _on_request)
        page.on("response", _on_response)

    async def _cleanup():
        try:
            page.remove_listener("response", _sniff_attendance_duration)
        except Exception:
            pass
        if _on_request:
            try:
                page.remove_listener("request", _on_request)
            except Exception:
                pass
        if _on_response:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass
        if _using_fake_video:
            try:
                await page.unroute("**/*.mp4")
            except Exception:
                pass
        try:
            await page.unroute("**/commons.ssu.ac.kr/em/**")
        except Exception:
            pass
        if _block_flash_global_handler:
            try:
                await page.unroute("**/flashErrorPage.html", _block_flash_global_handler[0])
            except Exception:
                pass

    try:
        return await _play_lecture_inner(
            page,
            lecture_url,
            on_progress,
            debug,
            fallback_duration,
            log,
            state,
            _using_fake_video,
            _sniffed_duration,
            _fake_video_cache,
            _shared_duration,
        )
    except asyncio.CancelledError:
        state.error = "사용자 중단"
        state.cancelled = True
        return state
    finally:
        await _cleanup()


async def _play_lecture_inner(
    page: Page,
    lecture_url: str,
    on_progress: Callable[[PlaybackState], None] | None,
    debug: bool,
    fallback_duration: float,
    log: Callable,
    state: PlaybackState,
    _using_fake_video: bool,
    _sniffed_duration: list[float] | None = None,
    _fake_video_cache: list[bytes] | None = None,
    _shared_duration: list[float] | None = None,
) -> PlaybackState:
    """play_lecture()의 실제 재생 로직. try-finally로 _cleanup() 보장을 위해 분리."""
    await page.goto(lecture_url, wait_until="domcontentloaded", timeout=60000)
    log(f"    → 현재 URL: {page.url}")

    # 세션 만료 감지 → 재로그인 후 재이동
    if "login" in page.url:
        log("[1] 세션 만료 감지 — 재로그인 중...")
        from src.auth.login import ensure_logged_in
        from src.config import Config

        username = Config.LMS_USER_ID
        password = Config.LMS_PASSWORD
        ok = await ensure_logged_in(page, username, password)
        if not ok:
            state.error = "세션 만료 후 재로그인 실패"
            return state
        log("[1] 재로그인 완료 — 강의 페이지 재이동 중...")
        await page.goto(lecture_url, wait_until="domcontentloaded", timeout=60000)
        log(f"    → 재이동 후 URL: {page.url}")

    # 2. 초기 플레이어 선택 화면 frame 탐색 (재생 버튼이 있는 곳)
    log("[2] 플레이어 선택 화면 frame 탐색 중...")
    player_frame = await _find_player_frame(page)
    if not player_frame:
        log("    → 실패: tool_content 또는 commons.ssu.ac.kr frame 없음")
        log("    → 현재 프레임 목록:")
        for f in page.frames:
            log(f"       name={f.name!r}  url={f.url}")

        # learningx 플레이어 감지: tool_content가 learningx URL인 경우
        # LTI POST가 500으로 실패했을 가능성이 있으므로 networkidle까지 대기 후 재시도
        tool_frame = page.frame(name="tool_content")
        if tool_frame and "learningx" in tool_frame.url:
            log(f"    → learningx 플레이어 감지: {tool_frame.url}")
            log("    → networkidle 대기 후 frame 재탐색...")
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception as e:
                log(f"    → networkidle 대기 실패 (계속 진행): {e}")
            # networkidle 대기 중 learningx API로 duration을 미리 조회
            # endat=0.00 강의는 Plan A 진행 시 fallback_duration이 없으면 재생 실패하므로
            # 여기서 item_content_data.duration을 얻어 fallback_duration으로 사용
            lx_duration = await _fetch_learningx_duration(page, tool_frame.url, log)
            if lx_duration > 0:
                log(f"    → learningx API duration={lx_duration:.1f}s — fallback_duration으로 사용")
                fallback_duration = lx_duration

            player_frame = await _find_player_frame(page)
            if player_frame:
                log("    → networkidle 후 commons frame 발견 — Plan A 계속")
            else:
                log("    → networkidle 후에도 commons frame 없음 — learningx API Plan B")
                return await _play_via_learningx_api(page, tool_frame.url, on_progress, log, fallback_duration)
        else:
            state.error = "비디오 프레임을 찾지 못했습니다."
            return state
    # frame이 나중에 navigate되면 URL이 바뀌므로 지금 즉시 저장
    player_url_snapshot = player_frame.url
    log(f"    → 성공: {player_url_snapshot}")

    # 2.5. commons frame의 meta 태그에서 실제 영상 duration 확인
    # LectureItem.duration(강의 목록 표시용)이 실제와 다를 수 있으므로 commons HTML 기준으로 교정.
    # sniff로도 동일하게 교정 시도한다.
    try:
        meta_dur = 0.0
        # sniff duration 우선
        if _sniffed_duration:
            meta_dur = _sniffed_duration[0]
            log(f"[2.5] sniff duration={meta_dur:.1f}s")
        # sniff 없으면 commons meta 태그
        if not meta_dur:
            meta_dur = float(
                await player_frame.evaluate(
                    "() => { var m = document.querySelector('meta[name=\"commons.duration\"]'); "
                    "return m ? parseFloat(m.getAttribute('content')) : 0; }"
                )
                or 0
            )
            if meta_dur > 0:
                log(f"[2.5] commons meta duration={meta_dur:.1f}s")
        if meta_dur > 0:
            # _serve_fake 클로저가 참조하는 공유 컨테이너를 항상 최신 값으로 업데이트
            if _shared_duration is not None:
                _shared_duration[0] = meta_dur
            if abs(meta_dur - fallback_duration) > 10:
                log(f"    → fallback_duration({fallback_duration:.1f}s)과 차이 큼 — {meta_dur:.1f}s로 교정")
                fallback_duration = meta_dur
                # 이미 캐시된 경우(재요청) 캐시를 비워 재생성을 유도한다.
                if _fake_video_cache is not None and _fake_video_cache:
                    _fake_video_cache.clear()
                    log("    → fake webm 캐시 초기화 (다음 MP4 요청 시 재생성)")
    except Exception as e:
        log(f"[2.5] duration 교정 실패: {e}")

    # 3. 이어보기 다이얼로그 처리 (처음부터 재생)
    await asyncio.sleep(1)
    dismissed = await _dismiss_dialog(player_frame, restart=True)
    log(f"[3] 이어보기 다이얼로그: {'처리됨' if dismissed else '없음'}")

    # 4. 재생 버튼 클릭
    log(f"[4] 재생 버튼({_PLAY_BTN}) 클릭 시도...")
    clicked = await _click_play(player_frame)
    log(f"    → {'클릭 성공' if clicked else '버튼 없음 또는 타임아웃'}")

    # 이어보기 다이얼로그가 재생 버튼 클릭 후 뜨는 경우도 처리
    await asyncio.sleep(1)
    dismissed2 = await _dismiss_dialog(player_frame, restart=True)
    log(f"[4b] 재생 후 이어보기 다이얼로그: {'처리됨' if dismissed2 else '없음'}")

    # 5. 재생 버튼 클릭 후 video 태그가 있는 frame을 새로 탐색
    log("[5] video 태그가 있는 frame 재스캔 중 (재생 후 frame 구조 변경 대응)...")
    log("    → 현재 전체 frame 목록:")
    for f in page.frames:
        log(f"       name={f.name!r}  url={f.url}")

    # video frame 탐색: 최대 10초만 기다림 (실패 시 빠르게 진단)
    frame = None
    for _ in range(10):
        for f in page.frames:
            if "commons.ssu.ac.kr" not in f.url:
                continue
            try:
                count = await f.evaluate("() => document.querySelectorAll('video').length")
                if count > 0:
                    frame = f
                    break
            except Exception:
                pass
        if frame:
            break
        await asyncio.sleep(1)

    if not frame:
        log("    → video frame 없음. 진도 API 직접 호출 방식으로 전환...")
        log(f"    → player URL: {player_url_snapshot}")
        # sniff duration 우선 적용 (LMS 응답에서 직접 캡처한 값)
        if _sniffed_duration:
            fallback_duration = _sniffed_duration[0]
            log(f"    → sniff duration={fallback_duration:.1f}s → fallback_duration 적용")
        # sniff 실패 시 commons frame의 meta 태그에서 duration 추출
        if not _sniffed_duration:
            for f in page.frames:
                if "commons.ssu.ac.kr" not in f.url:
                    continue
                try:
                    meta_dur = await f.evaluate(
                        "() => { var m = document.querySelector('meta[name=\"commons.duration\"]'); "
                        "return m ? parseFloat(m.getAttribute('content')) : 0; }"
                    )
                    if meta_dur and meta_dur > 0:
                        fallback_duration = float(meta_dur)
                        log(f"    → commons meta duration={fallback_duration:.1f}s → fallback_duration 적용")
                        break
                except Exception as e:
                    log(f"    → commons meta duration 조회 실패 (frame={f.url[:60]}): {e}")
        # Plan A에서 열린 sl=1 commons 프레임을 Plan B에 전달:
        # sl=0 재로드 없이 동일 세션 컨텍스트에서 JSONP를 호출하므로 ErrAlreadyInView 방지
        return await _play_via_progress_api(
            page,
            player_url_snapshot,
            on_progress,
            log,
            fallback_duration,
            existing_commons_frame=player_frame,
        )
    log(f"    → video frame 발견: {frame.url}")

    # 6. video 요소 duration 대기
    log(f"[6] video 요소({_VIDEO_SEL}) duration 대기 (최대 {_PLAY_TIMEOUT}초)...")
    deadline = asyncio.get_event_loop().time() + _PLAY_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        info = await _get_video_state(frame)
        if debug:
            log(f"    poll: info={info}")
        if info and info["duration"] > 0:
            log(f"    → 영상 시작 확인: duration={info['duration']:.1f}s")
            break
        await asyncio.sleep(0.5)
    else:
        log("[6] 타임아웃. 페이지 상태 진단:")
        await _debug_page_state(page, frame, log)
        state.error = "영상이 시작되지 않았습니다."
        return state

    # 6.5. GetCurrentTime/GetTotalDuration 오버라이드
    # 가짜 WebM 재생 시 GetCurrentTime()이 apiManager 내부 상태(=0)를 반환해
    # afterTimeUpdate의 2초 진행 조건이 충족되지 않아 진도 API가 호출되지 않는 문제 수정.
    # video 요소에서 직접 읽도록 오버라이드하면 실제 재생 시간이 반영되어 진도 보고가 동작한다.
    if _using_fake_video:
        try:
            await frame.evaluate(f"""() => {{
                if (typeof GetCurrentTime !== 'undefined') {{
                    GetCurrentTime = function() {{
                        var v = document.querySelector('{_VIDEO_SEL}');
                        return v ? v.currentTime : 0;
                    }};
                }}
                if (typeof GetTotalDuration !== 'undefined') {{
                    GetTotalDuration = function() {{
                        var v = document.querySelector('{_VIDEO_SEL}');
                        return v ? v.duration : 0;
                    }};
                }}
                // sendPlayedTime 교체:
                // 원본 함수는 GetCumulativePlayedPage() = 10000000000000 (apiManager 비정상값)을
                // 그대로 URL에 포함 → 서버 400. 전역 접근 가능하므로 올바른 파라미터로 재구성한다.
                if (typeof sendPlayedTime !== 'undefined') {{
                    sendPlayedTime = function(stateVal) {{
                        if (typeof lms_url === 'undefined' || !lms_url) return;
                        var v = document.querySelector('{_VIDEO_SEL}');
                        if (!v) return;
                        var curTime = v.currentTime;
                        var totalPage = typeof GetTotalPage !== 'undefined' ? GetTotalPage() : 14;
                        var cumPage = Math.max(1, Math.ceil(curTime / v.duration * totalPage));
                        var ts = Date.now();
                        var cbName = 'jQuery111_' + ts;
                        var sep = lms_url.indexOf('?') >= 0 ? '&' : '?';
                        var url = lms_url + sep +
                            'callback=' + cbName +
                            '&state=' + stateVal +
                            '&duration=' + v.duration.toFixed(2) +
                            '&currentTime=' + curTime.toFixed(2) +
                            '&cumulativeTime=' + curTime.toFixed(2) +
                            '&page=' + cumPage +
                            '&totalpage=' + totalPage +
                            '&cumulativePage=' + cumPage +
                            '&_=' + ts;
                        window[cbName] = function(d) {{ delete window[cbName]; }};
                        var s = document.createElement('script');
                        s.src = url;
                        document.head.appendChild(s);
                    }};
                }}
                // isPlayedContent: 플레이어가 "재생 시작" 이벤트로 설정하는 플래그.
                // 가짜 WebM에서는 apiManager가 이 이벤트를 발생시키지 않으므로 강제로 true로 설정.
                if (typeof isPlayedContent !== 'undefined') {{
                    isPlayedContent = true;
                }}
                // afterPlayStateChange: 재생 시작 이벤트 강제 전송.
                // 서버가 START(play) 이벤트 수신 후에만 UPDATE 요청을 수락하는 경우 대비.
                // 가짜 WebM에서는 apiManager가 play state change를 발생시키지 않으므로 수동 호출.
                try {{
                    if (typeof afterPlayStateChange === 'function') {{
                        afterPlayStateChange('play');
                    }}
                }} catch(e) {{}}
            }}""")
            log("[6.5] GetCurrentTime / GetTotalDuration 오버라이드 + isPlayedContent = true 설정 완료")
        except Exception as e:
            log(f"[6.5] 오버라이드 실패: {e}")

    # 6.6. lms_url / total_page 추출
    # 진도 API를 page 컨텍스트(canvas.ssu.ac.kr, 동일 오리진)에서 직접 호출하기 위해
    # commons frame에서 lms_url을 읽어 Python 변수로 저장한다.
    _lms_url: str = ""
    _total_page: int = 14
    if _using_fake_video:
        try:
            _lms_url = await frame.evaluate("() => typeof lms_url !== 'undefined' ? lms_url : ''")
            _total_page = int(await frame.evaluate("() => typeof GetTotalPage !== 'undefined' ? GetTotalPage() : 14"))
            log(f"[6.6] lms_url={_lms_url[:80]!r}... total_page={_total_page}")
        except Exception as e:
            log(f"[6.6] lms_url 추출 실패: {e}")

    if debug:
        try:
            js_info = await frame.evaluate("""() => {
                var funcs = [];
                if (window.apiManager) {
                    Object.keys(window.apiManager).forEach(function(k) {
                        if (typeof window.apiManager[k] === 'function') funcs.push(k);
                    });
                }
                return JSON.stringify({
                    afterTimeUpdate: typeof afterTimeUpdate,
                    afterTimeUpdateFull: typeof afterTimeUpdate !== 'undefined'
                        ? afterTimeUpdate.toString() : null,
                    afterPlayStateChange: typeof afterPlayStateChange,
                    apiManagerType: typeof window.apiManager,
                    apiManagerFunctions: funcs.slice(0, 30),
                    launcherType: typeof window.launcher,
                    playTime: typeof play_time !== 'undefined' ? play_time : 'undefined',
                    lmsUrl: typeof lms_url !== 'undefined'
                        ? (lms_url.length > 0 ? lms_url.slice(0, 120) : '(empty)') : 'undefined',
                    getCurrentTimeResult: typeof GetCurrentTime !== 'undefined'
                        ? GetCurrentTime() : 'undefined',
                    getTotalDurationResult: typeof GetTotalDuration !== 'undefined'
                        ? GetTotalDuration() : 'undefined',
                    isPlayedContent: typeof isPlayedContent !== 'undefined'
                        ? isPlayedContent : 'undefined(closure?)',
                    percentStep1: typeof PERCENT_STEP1 !== 'undefined'
                        ? PERCENT_STEP1 : 'undefined(closure?)',
                    percentStep2: typeof PERCENT_STEP2 !== 'undefined'
                        ? PERCENT_STEP2 : 'undefined(closure?)',
                    isPercentStep1Complete: typeof isPercentStep1Complete !== 'undefined'
                        ? isPercentStep1Complete : 'undefined(closure?)',
                    sendPlayedTimeDefined: typeof sendPlayedTime !== 'undefined',
                    afterPlayStateChangeFull: typeof afterPlayStateChange !== 'undefined'
                        ? afterPlayStateChange.toString() : null,
                });
            }""")
            log(f"  [진단] 플레이어 JS 상태: {js_info}")
        except Exception as e:
            log(f"  [진단] 플레이어 JS 상태 조회 실패: {e}")

    # 7. 재생 완료까지 폴링
    log("[7] 재생 루프 시작")
    _AFTER_UPDATE_INTERVAL = 30.0  # afterTimeUpdate 수동 호출 주기 (초)
    _last_after_update = asyncio.get_event_loop().time() - _AFTER_UPDATE_INTERVAL  # 즉시 첫 호출
    while True:
        info = await _get_video_state(frame)
        if info is None:
            # frame이 언로드된 경우
            log("[7] video state가 None — frame 언로드됨")
            break

        state.current = info["current"]
        state.duration = info["duration"]
        state.ended = info["ended"]

        if on_progress:
            on_progress(state)

        if info["ended"]:
            log("[7] 영상 ended=True — 완료")
            break

        # duration - threshold 이상 재생됐으면 완료로 간주
        if state.duration > 0 and state.current >= state.duration - _END_THRESHOLD:
            state.ended = True
            if on_progress:
                on_progress(state)
            log("[7] 재생 완료 기준 도달")
            break

        # 일시정지 상태면 강제 재생 (LMS 자동 정지 방지)
        if info["paused"]:
            log("[7] 일시정지 감지 → 강제 재생")
            await _ensure_playing(frame)

        # 30초마다 afterTimeUpdate() 수동 호출
        # 가짜 WebM 재생 시 apiManager가 timeupdate 이벤트를 발생시키지 않아
        # afterTimeUpdate가 자동으로 호출되지 않는 경우를 보완한다.
        # afterTimeUpdate는 commons frame 내에서 sl=1 세션 컨텍스트로 실행되므로
        # 직접 진도 API를 호출해도 ErrAlreadyInView가 발생하지 않는다.
        if _using_fake_video:
            now = asyncio.get_event_loop().time()
            if now - _last_after_update >= _AFTER_UPDATE_INTERVAL:
                # ── 진도 API를 page 컨텍스트(canvas.ssu.ac.kr, 동일 오리진)에서 fetch ──
                # frame 컨텍스트(commons.ssu.ac.kr)에서 script 태그로 호출하면
                # 크로스오리진 요청이 되어 SameSite 쿠키가 전송되지 않음 → 빈 400.
                # page 컨텍스트는 canvas.ssu.ac.kr 동일 오리진이므로 쿠키가 자동 포함된다.
                if _lms_url and state.duration > 0:
                    cur = state.current
                    dur = state.duration
                    cum_page = max(1, math.ceil(cur / dur * _total_page))
                    ts = int(now * 1000)
                    sep = "&" if "?" in _lms_url else "?"
                    progress_url = (
                        f"{_lms_url}{sep}callback=_cb_{ts}&state=8"
                        f"&duration={dur:.2f}"
                        f"&currentTime={cur:.2f}&cumulativeTime={cur:.2f}"
                        f"&page={cum_page}&totalpage={_total_page}"
                        f"&cumulativePage={cum_page}&_={ts}"
                    )
                    try:
                        result = await page.evaluate(f"""
                            async () => {{
                                try {{
                                    const resp = await fetch({json.dumps(progress_url)});
                                    return {{s: resp.status, b: (await resp.text()).slice(0, 200)}};
                                }} catch(e) {{
                                    return {{s: -1, b: e.message}};
                                }}
                            }}
                        """)
                        log(
                            f"[7] 진도 API (page ctx): {result.get('s')} "
                            f"{result.get('b', '')!r} "
                            f"({cur:.0f}s / {dur:.0f}s)"
                        )
                    except Exception as e:
                        log(f"[7] 진도 API (page ctx) 실패: {e}")

                # ── afterTimeUpdate: play_time 상태 유지용 ──
                try:
                    await frame.evaluate("""() => {
                        try { isPlayedContent = true; } catch(e) {}
                        try {
                            // play_time을 현재 시간 직전으로 리셋:
                            // afterTimeUpdate의 seek 분기 조건 |cur - play_time| > 2 우회.
                            if (typeof play_time !== 'undefined' && typeof GetCurrentTime !== 'undefined') {
                                play_time = Math.max(0, GetCurrentTime() - 1);
                            }
                        } catch(e) {}
                        if (typeof afterTimeUpdate === 'function') afterTimeUpdate();
                    }""")
                    log(f"[7] afterTimeUpdate() 호출 ({state.current:.0f}s / {state.duration:.0f}s)")
                except Exception as e:
                    log(f"[7] afterTimeUpdate() 실패: {e}")
                _last_after_update = now

        await asyncio.sleep(_POLL_INTERVAL)

    # Plan A가 예상보다 훨씬 일찍 끝난 경우 (fake webm 고속 재생 등)
    # duration의 50% 미만에서 ended되면 Plan B로 전환해 progress API를 직접 호출한다.
    if state.ended and state.duration > 0 and state.current < state.duration * 0.5:
        log(f"[7] 영상이 예상보다 일찍 종료 ({state.current:.1f}s / {state.duration:.1f}s) — Plan B로 전환")
        if _sniffed_duration:
            fallback_duration = _sniffed_duration[0]
            log(f"    → sniff duration={fallback_duration:.1f}s → fallback_duration 적용")
        elif frame:
            try:
                meta_dur = await frame.evaluate(
                    "() => { var m = document.querySelector('meta[name=\"commons.duration\"]'); "
                    "return m ? parseFloat(m.getAttribute('content')) : 0; }"
                )
                if meta_dur and meta_dur > 0:
                    fallback_duration = float(meta_dur)
                    log(f"    → commons meta duration={fallback_duration:.1f}s → fallback_duration 적용")
            except Exception as e:
                log(f"    → commons meta duration 조회 실패 (Plan B): {e}")
        return await _play_via_progress_api(page, player_url_snapshot, on_progress, log, fallback_duration)

    # Plan A 완료 후 progress API에 100% 직접 보고
    # 플레이어 JS가 가짜 WebM 재생 중 progress API를 호출하지 않는 경우 대비
    await _report_completion(page, player_url_snapshot, state.duration, log, use_page_eval=True)

    return state
