"""
영상 다운로드.

Playwright로 LMS 강의 페이지에서 video URL을 추출한 뒤,
requests로 청크 스트리밍 다운로드한다.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from functools import partial
from http.client import IncompleteRead
from pathlib import Path

import requests
from playwright.async_api import Page

logger = logging.getLogger(__name__)

from src.player.background_player import _click_play, _dismiss_dialog, _find_player_frame  # noqa: E402

_MAX_RETRIES = 3
_TIMEOUT = (10, 60)  # (connect, read) seconds
_CHUNK_SIZE = 65536  # 64 KB


def _sanitize_filename(name: str) -> str:
    """파일명에 사용 불가한 문자를 제거한다."""
    sanitized = re.sub(r'[<>:"/\\|?*]', "", name)
    sanitized = re.sub(r"\.{2,}", "", sanitized)  # 상위 디렉토리 순회 방지
    sanitized = sanitized.strip(" .")
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized or "lecture"


async def extract_video_url(page: Page, lecture_url: str) -> str | None:
    """
    LMS 강의 페이지에서 mp4 URL을 추출한다.

    Plan A: video 태그 src 폴링 (일반 타입)
    Plan B: Network 요청 가로채기 — mp4 URL이 포함된 요청 캡처 (readystream 등)
    """
    import asyncio

    captured: dict = {"url": None}

    exclude_patterns = ("preloader.mp4", "preview.mp4", "thumbnail.mp4")

    def _is_valid_mp4(url: str) -> bool:
        return ".mp4" in url and not any(p in url for p in exclude_patterns)

    def _on_request(request):
        url = request.url
        if _is_valid_mp4(url) and captured["url"] is None:
            captured["url"] = url

    def _on_response(response):
        url = response.url
        if _is_valid_mp4(url) and captured["url"] is None:
            captured["url"] = url
        # content.php 응답에서 미디어 URL 추출
        if "content.php" in url and "commons.ssu.ac.kr" in url:

            async def _parse_content_php():
                try:
                    import xml.etree.ElementTree as ET

                    body = await response.text()
                    root = ET.fromstring(body)
                    # desktop > html5 > media_uri (progressive) 우선
                    # mobile > html5 > media_uri fallback
                    media_uri = None

                    # 구조 A: content_playing_info > main_media > desktop/html5/media_uri
                    for path in (
                        "content_playing_info/main_media/desktop/html5/media_uri",
                        "content_playing_info/main_media/mobile/html5/media_uri",
                        ".//main_media//html5/media_uri",
                    ):
                        el = root.find(path)
                        if el is not None and el.text and el.text.strip():
                            candidate = el.text.strip()
                            if "[" not in candidate:
                                media_uri = candidate
                                break

                    # 구조 B: service_root > media > media_uri[@method="progressive"]
                    # [MEDIA_FILE] 플레이스홀더를 story_list의 실제 파일명으로 치환
                    if not media_uri:
                        media_uri_el = root.find("service_root/media/media_uri[@method='progressive']")
                        if media_uri_el is not None and media_uri_el.text:
                            url_template = media_uri_el.text.strip()
                            if "[MEDIA_FILE]" in url_template:
                                # main_media 텍스트에서 실제 파일명 추출
                                main_media_el = root.find(".//story_list/story/main_media_list/main_media")
                                if main_media_el is not None and main_media_el.text:
                                    media_uri = url_template.replace("[MEDIA_FILE]", main_media_el.text.strip())
                            elif "[" not in url_template:
                                media_uri = url_template

                    if media_uri and captured["url"] is None:
                        captured["url"] = media_uri
                except Exception as e:
                    print(f"  [NET] content.php 파싱 오류: {e}")

            _task = asyncio.ensure_future(_parse_content_php())  # noqa: RUF006

    page.on("request", _on_request)
    page.on("response", _on_response)

    try:
        # print(f"  [DBG] 페이지 이동: {lecture_url[:80]}")
        await page.goto(lecture_url, wait_until="domcontentloaded", timeout=60000)
        # iframe + content.php 로드 대기 (비동기 파싱 완료까지)
        for _ in range(20):  # 최대 10초
            await asyncio.sleep(0.5)
            if captured["url"]:
                break
        # print(f"  [DBG] 현재 페이지 URL: {page.url[:80]}")

        # content.php에서 미디어 URL이 추출됐으면 바로 반환
        if captured["url"]:
            # print(f"  [NET] content.php에서 미디어 URL 추출 성공: {captured['url']}")
            return captured["url"]

        player_frame = await _find_player_frame(page)
        if not player_frame:
            # print("  [DBG] player frame을 찾지 못했습니다.")
            # for f in page.frames:
            #     print(f"  [DBG]   {f.url[:100]}")
            return None

        # print(f"  [DBG] player frame 발견: {player_frame.url[:80]}")

        # 이어보기 다이얼로그 처리 후 재생 버튼 클릭
        await asyncio.sleep(1)
        await _dismiss_dialog(player_frame, restart=True)
        await _click_play(player_frame)
        # print(f"  [DBG] 재생 버튼 클릭: {'성공' if clicked else '실패(버튼 없음)'}")
        await asyncio.sleep(1)
        await _dismiss_dialog(player_frame, restart=True)

        # 최대 60초 폴링: Plan A(video DOM) + Plan B(network 캡처) 동시 확인
        # 재생 후 새로운 frame이 생성될 수 있으므로 page.frames 전체를 매번 재스캔
        # 이어보기 다이얼로그도 매 폴링마다 체크 (재생 도중 뒤늦게 뜨는 경우 대응)
        dialog_dismissed = False
        for _i in range(120):
            # Plan B 먼저 확인 (network에서 이미 캡처됐을 수 있음)
            if captured["url"]:
                return captured["url"]

            # 이어보기 다이얼로그가 재생 도중 뒤늦게 뜨는 경우 처리
            if not dialog_dismissed:
                dialog_dismissed = await _dismiss_dialog(player_frame, restart=True)

            # Plan A: 모든 commons frame에서 video 태그 src 확인 (재생 후 새 frame 포함)
            commons_frames = [f for f in page.frames if "commons.ssu.ac.kr" in f.url]
            # if i % 10 == 0:
            #     print(f"  [DBG] 폴링({i}): commons frame 수={len(commons_frames)}")
            #     for fi, f in enumerate(commons_frames):
            #         print(f"  [DBG]   commons[{fi}]: {f.url[:80]}")

            for frame in commons_frames:
                try:
                    # get_attribute 방식으로 직접 조회 (evaluate보다 안정적)
                    video_el = await frame.query_selector("video.vc-vplay-video1")
                    if video_el:
                        src = await video_el.get_attribute("src")
                        # if i % 10 == 0:
                        #     print(f"  [DBG]   vc-vplay-video1 src: {str(src)[:80]}")
                        if src and src.startswith("http") and ".mp4" in src:
                            return src

                    # fallback: 모든 video 태그 확인
                    result = await frame.evaluate("""() => {
                        const videos = document.querySelectorAll('video');
                        for (const v of videos) {
                            const src = v.src || v.currentSrc || '';
                            if (src && src.startsWith('http') && src.includes('.mp4')) return src;
                        }
                        return null;
                    }""")
                    # if i % 10 == 0:
                    #     print(f"  [DBG]   fallback video.src: {str(result)[:80]}")
                    if result:
                        return result
                except Exception as e:
                    logger.debug("frame 평가 오류 (계속 탐색): %s", e)

            await asyncio.sleep(0.5)

        # 폴링 종료 (60초) — 아래 디버그 코드는 URL 추출 실패 시 원인 분석용
        # print("  [DBG] 60초 폴링 종료. player 설정 파일 분석...")

        # async def _fetch_text(url: str) -> str:
        #     try:
        #         resp = await page.request.get(url)
        #         if resp.status != 200:
        #             return ""
        #         raw = await resp.body()
        #         for enc in ("utf-8", "euc-kr", "cp949", "latin-1"):
        #             try:
        #                 return raw.decode(enc)
        #             except Exception:
        #                 continue
        #         return raw.decode("latin-1")
        #     except Exception as e:
        #         print(f"  [DBG] fetch 오류 {url}: {e}")
        #         return ""

        # uni-player.min.js — m3u8/HLS URL 조합 로직 분석
        # import re as _re
        # player_js_url = next((u for u in all_requests if "uni-player.min.js" in u), None)
        # if player_js_url:
        #     print(f"  [DBG] uni-player.min.js fetch 중...")
        #     text = await _fetch_text(player_js_url)
        #     print(f"  [DBG] uni-player.min.js 크기: {len(text)} bytes")
        #     matches = _re.findall(r'.{0,150}(?:m3u8|\.m3u|hls(?:Url|Path|Src)|readystream|stream_url|streamUrl|videoSrc|mediaSrc|contentUri|content_uri|upf|ssmovie).{0,150}', text)
        #     print(f"  [DBG] uni-player.min.js 관련 키워드 ({len(matches)}개):")
        #     for m in matches[:40]:
        #         print(f"  [DBG]   {m.strip()[:300]}")

        return captured["url"]

    finally:
        page.remove_listener("request", _on_request)
        page.remove_listener("response", _on_response)


async def download_video_with_browser(
    page,
    url: str,
    save_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Playwright 브라우저 컨텍스트의 쿠키를 사용해 영상을 스트리밍 다운로드한다."""
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # Playwright 컨텍스트에서 쿠키 추출 → requests에 전달 (CDN 인증 자동 처리)
    context_cookies = await page.context.cookies()
    cookies = {c["name"]: c["value"] for c in context_cookies}

    referer = "https://commons.ssu.ac.kr/"
    last_error = None
    loop = asyncio.get_running_loop()
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            await loop.run_in_executor(
                None,
                partial(
                    _stream_download, url, save_path, on_progress, attempt=attempt, cookies=cookies, referer=referer
                ),
            )
            return save_path.resolve()
        except Exception as e:
            last_error = e
            # 부분 파일은 유지 — 다음 시도에서 Range 헤더로 이어받기
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(2**attempt)
    _remove_partial(save_path)
    raise last_error


def download_video(
    url: str,
    save_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
    cookies: dict | None = None,
    referer: str | None = None,
) -> Path:
    """
    HTTP 스트리밍으로 영상을 다운로드한다.

    Args:
        url:         직접 다운로드 가능한 mp4 URL
        save_path:   저장 경로 (파일명 포함)
        on_progress: (downloaded_bytes, total_bytes) 콜백

    Returns:
        저장된 파일의 Path

    Raises:
        Exception: 최대 재시도 후에도 실패한 경우
    """
    save_path.parent.mkdir(parents=True, exist_ok=True)

    last_error = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            _stream_download(url, save_path, on_progress, attempt, cookies=cookies, referer=referer)
            return save_path.resolve()
        except (IncompleteRead, requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            _remove_partial(save_path)
            if attempt < _MAX_RETRIES:
                wait = 2**attempt
                time.sleep(wait)
        except Exception as e:
            last_error = e
            _remove_partial(save_path)
            break

    raise last_error


def _stream_download(
    url: str,
    save_path: Path,
    on_progress: Callable[[int, int], None] | None,
    attempt: int,
    cookies: dict | None = None,
    referer: str | None = None,
):
    headers = {"Referer": referer} if referer else {}
    existing_size = 0

    # 재시도 시 기존 파일이 있으면 이어받기 시도
    if attempt > 1 and save_path.exists():
        existing_size = save_path.stat().st_size
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

    response = requests.get(url, stream=True, timeout=_TIMEOUT, cookies=cookies, headers=headers)

    if response.status_code == 206:
        # 서버가 Range 지원 → 이어받기
        mode = "ab"
        total = existing_size + int(response.headers.get("content-length", 0))
        downloaded = existing_size
    elif response.status_code == 200:
        # 서버가 Range 미지원 또는 첫 시도 → 처음부터
        response.raise_for_status()
        mode = "wb"
        total = int(response.headers.get("content-length", 0))
        downloaded = 0
    else:
        response.raise_for_status()
        return

    with open(save_path, mode) as f:
        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress and total > 0:
                    on_progress(downloaded, total)


def _remove_partial(path: Path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def make_filepath(course_name: str, week_label: str, lecture_title: str) -> Path:
    """'과목명/N주차/강의명.mp4' 형식의 상대 경로를 생성한다."""
    course = _sanitize_filename(course_name)
    title = _sanitize_filename(lecture_title)

    # week_label에서 "N주차" 추출 (예: "1주차(총 8주 중)" → "1주차")
    week_match = re.match(r"(\d+주차)", week_label or "")
    week_dir = week_match.group(1) if week_match else _sanitize_filename(week_label or "") or "기타"

    return Path(course) / week_dir / f"{title}.mp4"
