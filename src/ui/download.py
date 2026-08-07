"""
다운로드 관련 UI.

다운로드 진행률 화면을 제공한다.
다운로드 경로는 Config의 고정 경로(/download)를 사용한다.
"""

import asyncio
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)
from rich.text import Text

from src.config import Config
from src.logger import close_error_logger, get_error_logger

console = Console()


async def run_download(page, lec, course, audio_only: bool = False, both: bool = False) -> bool:
    """
    강의 영상을 다운로드하고 진행률을 Progress bar로 표시한다.

    Args:
        page:       CourseScraper._page (Playwright Page)
        lec:        다운로드할 LectureItem
        course:     과목 Course (파일명 생성에 사용)
        audio_only: True면 mp3로 변환 후 mp4 삭제
        both:       True면 mp4 유지 + mp3도 추가 생성

    Returns:
        True: 정상 완료 / False: 오류
    """
    from src.converter.audio_converter import convert_to_mp3
    from src.downloader.video_downloader import download_video_with_browser, extract_video_url, make_filepath

    console.print()
    console.print(
        Panel(
            Text(lec.title, justify="center", style="bold cyan"),
            border_style="cyan",
            padding=(0, 4),
        )
    )
    console.print()

    download_dir = Config.get_download_dir()

    def _tg_error(msg_fn):
        """텔레그램 오류 알림을 전송한다 (설정된 경우에만)."""
        creds = Config.get_telegram_credentials()
        if creds:
            msg_fn(creds[0], creds[1])

    # 1. learningx 타입 조기 감지 (구조적으로 다운로드 불가)
    if "learningx" in lec.full_url:
        console.print("  [yellow]다운로드 불가:[/yellow] 이 강의는 다운로드가 지원되지 않는 형식입니다.")
        from src.notifier.telegram_notifier import notify_download_unsupported

        _tg_error(lambda t, c: notify_download_unsupported(t, c, course.long_name, lec.week_label, lec.title))
        return False

    # 2. video URL 추출 (최대 3회 재시도)
    _MAX_URL_RETRIES = 3
    _RETRY_WAIT = 10  # seconds

    video_url = None
    for attempt in range(1, _MAX_URL_RETRIES + 1):
        if attempt == 1:
            console.print("  [dim]영상 URL 추출 중...[/dim]")
        else:
            console.print(f"  [dim]영상 URL 추출 재시도 ({attempt}/{_MAX_URL_RETRIES})...[/dim]")
        video_url = await extract_video_url(page, lec.full_url)
        if video_url:
            break
        if attempt < _MAX_URL_RETRIES:
            console.print(f"  [yellow]URL 추출 실패. {_RETRY_WAIT}초 후 재시도합니다...[/yellow]")
            await asyncio.sleep(_RETRY_WAIT)

    if not video_url:
        console.print("  [bold red]오류:[/bold red] 영상 URL을 찾지 못했습니다. (3회 시도)")
        logger, log_path = get_error_logger("download")
        logger.info(f"강의: {lec.title}")
        logger.info(f"URL: {lec.full_url}")
        logger.info("오류: 영상 URL 추출 실패 (3회 재시도 후에도 실패)")
        close_error_logger(logger)
        console.print(f"  [dim]로그 저장: {log_path}[/dim]")
        from src.notifier.telegram_notifier import notify_download_error

        _tg_error(lambda t, c: notify_download_error(t, c, course.long_name, lec.week_label, lec.title))
        return False

    # 3. 파일 경로 결정
    mp4_relpath = make_filepath(course.long_name, lec.week_label, lec.title)
    mp4_path = (Path(download_dir) / mp4_relpath).resolve()
    base_dir = Path(download_dir).resolve()
    if not mp4_path.is_relative_to(base_dir):
        console.print("  [bold red]오류:[/bold red] 잘못된 다운로드 경로가 감지되었습니다.")
        return False

    if audio_only:
        final_path = mp4_path.with_suffix(".mp3")
    elif both:
        final_path = mp4_path  # mp4 + mp3 둘 다 저장
    else:
        final_path = mp4_path
    console.print(f"  [dim]저장 경로: {final_path}[/dim]")
    console.print()

    # 4. mp4 다운로드 + Progress bar
    progress = Progress(
        SpinnerColumn(),
        TextColumn("  [bold]{task.description}"),
        BarColumn(bar_width=36),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
        expand=False,
    )
    task_id = progress.add_task(lec.title[:40], total=None)

    try:
        with Live(progress, console=console, refresh_per_second=8):

            def on_progress(downloaded: int, total: int):
                progress.update(task_id, completed=downloaded, total=total)

            await download_video_with_browser(page, video_url, mp4_path, on_progress=on_progress)
    except Exception as e:
        console.print(f"  [bold red]다운로드 실패:[/bold red] {e}")
        logger, log_path = get_error_logger("download")
        logger.info(f"강의: {lec.title}")
        logger.info(f"URL: {lec.full_url}")
        logger.info("영상 URL: [CDN URL 로그 제외]")
        logger.error(f"다운로드 실패: {e}")
        close_error_logger(logger)
        console.print(f"  [dim]로그 저장: {log_path}[/dim]")
        from src.notifier.telegram_notifier import notify_download_error

        _tg_error(lambda t, c: notify_download_error(t, c, course.long_name, lec.week_label, lec.title))
        return False

    # 5. mp3 변환 (audio_only 또는 both)
    mp3_path: Path | None = None
    if audio_only or both:
        console.print()
        console.print("  [dim]mp3 변환 중...[/dim]")
        try:
            mp3_path = convert_to_mp3(mp4_path)
            if audio_only:
                mp4_path.unlink()  # 음성 전용: 원본 mp4 삭제
        except Exception as e:
            console.print(f"  [bold red]mp3 변환 실패:[/bold red] {e}")
            return False

        console.print()
        console.print("  [bold green]다운로드 완료![/bold green]")
        if both:
            console.print(f"  [dim]{mp4_path}[/dim]")
        console.print(f"  [dim]{mp3_path}[/dim]")
    else:
        console.print()
        console.print("  [bold green]다운로드 완료![/bold green]")
        console.print(f"  [dim]{mp4_path}[/dim]")

    # 6. STT 변환 (mp3가 있고 STT_ENABLED=true인 경우)
    txt_path = None
    if mp3_path and Config.STT_ENABLED == "true":
        console.print()
        console.print("  [dim]STT 변환 중... (시간이 걸릴 수 있습니다)[/dim]")
        try:
            from src.stt.transcriber import transcribe

            txt_path = transcribe(
                mp3_path,
                model_size=Config.WHISPER_MODEL or "base",
                language=Config.STT_LANGUAGE,
            )
            console.print("  [bold green]STT 완료![/bold green]")
            console.print(f"  [dim]{txt_path}[/dim]")
            if Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE == "true":
                mp3_path.unlink(missing_ok=True)
                console.print("  [dim]STT 변환 후 mp3 파일을 삭제했습니다.[/dim]")
        except Exception as e:
            console.print(f"  [bold red]STT 실패:[/bold red] {e}")

    # 7. AI 요약 (txt가 있고 AI_ENABLED=true인 경우)
    summary_path = None
    if txt_path and Config.AI_ENABLED == "true":
        api_key = Config.GOOGLE_API_KEY
        model = Config.GEMINI_MODEL
        if not api_key:
            console.print("  [yellow]AI 요약 건너뜀: API 키가 설정되지 않았습니다.[/yellow]")
        else:
            import warnings
            from concurrent.futures import ThreadPoolExecutor

            from src.summarizer.summarizer import GEMINI_DEFAULT_MODEL, summarize

            console.print()
            spinner_progress = Progress(
                SpinnerColumn(),
                TextColumn("  [bold]{task.description}"),
                TimeElapsedColumn(),
                console=console,
                expand=False,
            )
            task_id = spinner_progress.add_task("AI 요약 중...", total=None)

            _MAX_RETRIES = 3
            _RETRY_DELAY = 5
            last_error = None
            for attempt in range(_MAX_RETRIES):
                try:
                    if attempt > 0:
                        spinner_progress.update(
                            task_id, description=f"AI 요약 중... (재시도 {attempt}/{_MAX_RETRIES - 1})"
                        )
                        await asyncio.sleep(_RETRY_DELAY)
                    with Live(spinner_progress, console=console, refresh_per_second=8):
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            loop = asyncio.get_event_loop()
                            with ThreadPoolExecutor() as pool:
                                summary_path = await loop.run_in_executor(
                                    pool,
                                    lambda: summarize(
                                        txt_path,
                                        agent=Config.AI_AGENT or "gemini",
                                        api_key=api_key,
                                        model=model or GEMINI_DEFAULT_MODEL,
                                        prompt_template=Config.get_summary_prompt_template(),
                                        extra_prompt=Config.SUMMARY_PROMPT_EXTRA,
                                        course_name=course.long_name,
                                    ),
                                )
                    console.print("  [bold green]AI 요약 완료![/bold green]")
                    console.print(f"  [dim]{summary_path}[/dim]")
                    if Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE == "true" and txt_path:
                        txt_path.unlink(missing_ok=True)
                        console.print("  [dim]AI 요약 후 원본 txt 파일을 삭제했습니다.[/dim]")
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    if attempt < _MAX_RETRIES - 1:
                        console.print(f"  [yellow]AI 요약 실패 (재시도 예정): {e}[/yellow]")
            if last_error is not None:
                console.print(f"  [bold red]AI 요약 실패:[/bold red] {last_error}")

    # 8. 텔레그램 알림 (AI 요약 완료 시)
    if summary_path and Config.TELEGRAM_ENABLED == "true":
        tg_token = Config.TELEGRAM_BOT_TOKEN
        tg_chat_id = Config.TELEGRAM_CHAT_ID
        if tg_token and tg_chat_id:
            from src.notifier.telegram_notifier import notify_summary_complete, notify_summary_send_error

            console.print()
            console.print("  [dim]텔레그램으로 요약 전송 중...[/dim]")

            summary_text = summary_path.read_text(encoding="utf-8").strip()

            # 자동 삭제 대상 파일 목록
            files_to_delete = None
            if Config.TELEGRAM_AUTO_DELETE == "true":
                files_to_delete = [f for f in [mp4_path, mp3_path, txt_path, summary_path] if f]

            ok = notify_summary_complete(
                bot_token=tg_token,
                chat_id=tg_chat_id,
                course_name=course.long_name,
                week_label=lec.week_label,
                lecture_title=lec.title,
                summary_text=summary_text,
                summary_path=summary_path,
                auto_delete_files=files_to_delete,
            )
            if ok:
                console.print("  [bold green]텔레그램 전송 완료![/bold green]")
                if files_to_delete:
                    console.print("  [dim]파일이 자동 삭제되었습니다.[/dim]")
            else:
                console.print("  [yellow]텔레그램 전송 실패. 파일은 유지됩니다.[/yellow]")
                notify_summary_send_error(tg_token, tg_chat_id, course.long_name, lec.week_label, lec.title)

    return True
