"""
설정 UI.

최초 실행 시 또는 'setting' 명령으로 진입하는 설정 화면.
다운로드 규칙, STT, AI 요약 항목을 순서대로 질의한다.
"""

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from src.config import Config
from src.summarizer.summarizer import GEMINI_DEFAULT_MODEL, GEMINI_MODEL_IDS, GEMINI_MODEL_LABELS

console = Console()

_WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]


def run_settings() -> None:
    """
    설정 화면을 표시하고 결과를 SQLite 설정 DB에 저장한다.
    최초 실행 또는 'setting' 입력 시 호출된다.
    """
    console.clear()
    console.print(
        Panel(
            Text("설정", justify="center", style="bold cyan"),
            border_style="cyan",
            padding=(0, 4),
        )
    )
    console.print()
    console.print("  [dim]Enter를 누르면 현재 값(또는 기본값)을 유지합니다.[/dim]")
    console.print()

    download_dir = Config.get_download_dir()
    console.print(f"  [dim]다운로드 저장 위치는 {download_dir} 로 고정됩니다.[/dim]")
    console.print()

    # ── 1. 다운로드 규칙 ─────────────────────────────────────────
    _print_section("1. 다운로드 규칙")
    current_rule = Config.get_download_rule()
    _current = {"mp4": "영상만 (mp4)", "mp3": "음성만 (mp3)", "both": "영상 + 음성"}.get(current_rule, "영상만 (mp4)")
    console.print(f"  [dim]현재값: {_current}[/dim]")
    console.print()
    console.print("  [bold]1.[/bold] 영상만  [dim](mp4)[/dim]")
    console.print("  [bold]2.[/bold] 음성만  [dim](mp3)[/dim]")
    console.print("  [bold]3.[/bold] 영상 + 음성  [dim](mp4 + mp3)[/dim]")
    console.print()

    _rule_default = {"mp4": "1", "mp3": "2", "both": "3"}.get(current_rule, "1")
    rule_choice = Prompt.ask("  선택", choices=["1", "2", "3"], default=_rule_default, show_choices=False)
    download_rule = {"1": "mp4", "2": "mp3", "3": "both"}[rule_choice]
    console.print()

    # ── 1.1. STT (텍스트 변환) — 영상만이 아닌 경우만 ─────────────
    stt_enabled = False
    stt_delete_audio_after_transcribe = False
    if download_rule != "mp4":
        _print_section("1.1. 텍스트 변환 (STT)")
        console.print("  [dim]음성에서 텍스트를 추출합니다 (Whisper 로컬 실행).[/dim]")
        _stt_default = "y" if Config.STT_ENABLED == "true" else "n"
        stt_choice = Prompt.ask("  STT 사용", choices=["y", "n"], default=_stt_default, show_choices=True)
        stt_enabled = stt_choice == "y"

        if stt_enabled:
            # STT 언어 설정
            _print_section("  STT 언어")
            console.print("  [dim]ko: 한국어 고정 / en: 영어 고정 / auto: 자동 감지[/dim]")
            _lang_current = Config.STT_LANGUAGE or "ko"
            _lang_default = _lang_current if _lang_current in ("ko", "en", "auto") else "ko"
            lang_choice = Prompt.ask(
                "  STT 언어", choices=["ko", "en", "auto"], default=_lang_default, show_choices=True
            )
            stt_language = "" if lang_choice == "auto" else lang_choice
            Config.STT_LANGUAGE = stt_language
            Config._save_settings_values({"STT_LANGUAGE": stt_language})
            console.print()

            _print_section("  Whisper 모델 크기")
            console.print("  [dim]작을수록 빠르지만 정확도 낮음 (기본: base)[/dim]")
            for i, m in enumerate(_WHISPER_MODELS, 1):
                console.print(f"  [bold]{i}.[/bold] {m}")
            console.print()
            _model_default = (
                str(_WHISPER_MODELS.index(Config.WHISPER_MODEL) + 1) if Config.WHISPER_MODEL in _WHISPER_MODELS else "2"
            )
            model_choice = Prompt.ask(
                "  모델 선택", choices=[str(i) for i in range(1, 6)], default=_model_default, show_choices=False
            )
            Config.WHISPER_MODEL = _WHISPER_MODELS[int(model_choice) - 1]
            Config._save_settings_values({"WHISPER_MODEL": Config.WHISPER_MODEL})

            _print_section("  STT 후 mp3 삭제")
            console.print("  [dim]STT 변환 성공 후 생성된 mp3 파일을 삭제합니다.[/dim]")
            _delete_default = "y" if Config.STT_DELETE_AUDIO_AFTER_TRANSCRIBE == "true" else "n"
            delete_choice = Prompt.ask("  mp3 삭제", choices=["y", "n"], default=_delete_default, show_choices=True)
            stt_delete_audio_after_transcribe = delete_choice == "y"
        console.print()

    # ── 2. AI 요약 ───────────────────────────────────────────────
    _print_section("2. AI 요약")
    console.print("  [dim]STT로 변환된 텍스트를 AI로 자동 요약합니다.[/dim]")
    console.print("  [dim]Gemini API 키와 모델 설정이 있어야 사용할 수 있습니다.[/dim]")
    console.print("  [dim]현재 Gemini API를 지원합니다 (무료 티어 사용 가능).[/dim]")
    console.print()
    _ai_default = "y" if Config.AI_ENABLED == "true" else "n"
    ai_choice = Prompt.ask("  AI 요약 사용", choices=["y", "n"], default=_ai_default, show_choices=True)
    ai_enabled = ai_choice == "y"
    console.print()

    ai_agent = "gemini"
    api_key = ""
    gemini_model = Config.GEMINI_MODEL or GEMINI_DEFAULT_MODEL
    summary_prompt_template = Config.get_summary_prompt_template()
    summary_prompt_extra = Config.SUMMARY_PROMPT_EXTRA

    if ai_enabled:
        # 2.1. Gemini API 키
        _print_section("2.1. Gemini API 키 입력")
        console.print("  [dim]Google AI Studio에서 무료로 발급 가능합니다.[/dim]")
        _existing_key = Config.GOOGLE_API_KEY
        if _existing_key:
            console.print(f"  [dim]현재 키: {_existing_key[:8]}{'*' * 20}[/dim]")
            console.print("  [dim]변경하지 않으려면 Enter를 누르세요.[/dim]")
        console.print()

        raw_key = Prompt.ask("  API 키", default="").strip()
        api_key = raw_key if raw_key else _existing_key
        console.print()

        # API 키가 있을 때만 모델/프롬프트 선택
        if api_key:
            # 2.2. Gemini 모델 선택
            _print_section("2.2. Gemini 모델 선택")
            console.print(f"  [dim]고속·저비용 모델 권장 (기본값: {GEMINI_DEFAULT_MODEL})[/dim]")
            console.print()
            for i, label in enumerate(GEMINI_MODEL_LABELS, 1):
                console.print(f"  [bold]{i}.[/bold] {label}")
            console.print()

            _current_model = Config.GEMINI_MODEL or GEMINI_DEFAULT_MODEL
            _model_default = (
                str(GEMINI_MODEL_IDS.index(_current_model) + 1) if _current_model in GEMINI_MODEL_IDS else "1"
            )
            model_choice = Prompt.ask(
                "  모델 선택",
                choices=[str(i) for i in range(1, len(GEMINI_MODEL_IDS) + 1)],
                default=_model_default,
                show_choices=False,
            )
            gemini_model = GEMINI_MODEL_IDS[int(model_choice) - 1]
            console.print()

            # 2.3. 추가 요약 지시사항
            _print_section("2.3. 추가 요약 지시사항")
            console.print("  [dim]기본 요약 형식에 추가할 지시사항을 입력하세요.[/dim]")
            console.print("  [dim]예: '영어 용어는 원문 그대로 표기해줘', '코드 예시도 포함해줘'[/dim]")
            if Config.SUMMARY_PROMPT_EXTRA:
                console.print(
                    f"  [dim]현재값: {Config.SUMMARY_PROMPT_EXTRA[:60]}{'...' if len(Config.SUMMARY_PROMPT_EXTRA) > 60 else ''}[/dim]"
                )
                console.print("  [dim]비우려면 'clear'를 입력하세요.[/dim]")
            console.print()
            raw_extra = Prompt.ask("  추가 지시사항", default="").strip()
            if raw_extra.lower() == "clear":
                summary_prompt_extra = ""
            elif raw_extra:
                summary_prompt_extra = raw_extra
            else:
                summary_prompt_extra = Config.SUMMARY_PROMPT_EXTRA
            console.print()

            _print_section("2.4. AI 요약 후 원본 txt 삭제")
            console.print("  [dim]AI 요약 성공 후 STT 원본 텍스트 파일을 삭제합니다.[/dim]")
            _delete_txt_default = "y" if Config.SUMMARY_DELETE_TEXT_AFTER_SUMMARIZE == "true" else "n"
            delete_txt_choice = Prompt.ask(
                "  원본 txt 삭제", choices=["y", "n"], default=_delete_txt_default, show_choices=True
            )
            summary_delete_text_after_summarize = delete_txt_choice == "y"
            console.print()
        else:
            summary_delete_text_after_summarize = False
    else:
        summary_delete_text_after_summarize = False

    # ── 3. 텔레그램 알림 ─────────────────────────────────────────
    _print_section("3. 텔레그램 알림")
    console.print("  [dim]재생 완료 및 AI 요약 결과를 텔레그램 봇으로 받을 수 있습니다.[/dim]")
    console.print()
    _tg_default = "y" if Config.TELEGRAM_ENABLED == "true" else "n"
    tg_choice = Prompt.ask("  텔레그램 알림 사용", choices=["y", "n"], default=_tg_default, show_choices=True)
    tg_enabled = tg_choice == "y"
    console.print()

    tg_token = Config.TELEGRAM_BOT_TOKEN
    tg_chat_id = Config.TELEGRAM_CHAT_ID
    tg_auto_delete = Config.TELEGRAM_AUTO_DELETE == "true"

    if tg_enabled:
        # 3.1. 봇 토큰
        _print_section("3.1. 텔레그램 봇 토큰")
        console.print("  [dim]BotFather(@BotFather)에서 /newbot으로 발급받은 토큰을 입력하세요.[/dim]")
        if tg_token:
            console.print(f"  [dim]현재 토큰: {tg_token[:10]}{'*' * 20}[/dim]")
            console.print("  [dim]변경하지 않으려면 Enter를 누르세요.[/dim]")
        console.print()
        raw_token = Prompt.ask("  봇 토큰", default="").strip()
        if raw_token:
            tg_token = raw_token
        console.print()

        # 3.2. Chat ID
        _print_section("3.2. Chat ID")
        console.print("  [dim]@userinfobot에게 /start를 보내면 숫자로 된 Chat ID를 확인할 수 있습니다.[/dim]")
        if tg_chat_id:
            console.print(f"  [dim]현재 Chat ID: {tg_chat_id}[/dim]")
        console.print()
        raw_chat_id = Prompt.ask("  Chat ID", default=tg_chat_id or "").strip()
        if raw_chat_id:
            tg_chat_id = raw_chat_id
        console.print()

        # 연결 테스트
        if tg_token and tg_chat_id:
            console.print("  [dim]연결 테스트 중...[/dim]")
            from src.notifier.telegram_notifier import verify_bot

            ok, err = verify_bot(tg_token, tg_chat_id)
            if ok:
                console.print("  [bold green]텔레그램 연결 성공! 테스트 메시지를 확인하세요.[/bold green]")
            else:
                console.print(f"  [yellow]연결 테스트 실패: {err}[/yellow]")
                console.print("  [dim]설정은 저장되지만 알림이 전송되지 않을 수 있습니다.[/dim]")
            console.print()

        # 3.3. 자동 삭제 옵션
        _print_section("3.3. 요약 전송 후 파일 자동 삭제")
        console.print("  [dim]AI 요약 전송 성공 후 영상, 음성, STT 텍스트, 요약 파일을 모두 삭제합니다.[/dim]")
        _del_default = "y" if tg_auto_delete else "n"
        del_choice = Prompt.ask("  자동 삭제 사용", choices=["y", "n"], default=_del_default, show_choices=True)
        tg_auto_delete = del_choice == "y"
        console.print()

    # ── 저장 ────────────────────────────────────────────────────
    Config.save_settings(
        download_dir=download_dir,
        download_rule=download_rule,
        stt_enabled=stt_enabled,
        ai_enabled=ai_enabled,
        ai_agent=ai_agent,
        api_key=api_key,
        ai_model=gemini_model,
        summary_prompt_template=summary_prompt_template,
        summary_prompt_extra=summary_prompt_extra,
        stt_delete_audio_after_transcribe=stt_delete_audio_after_transcribe,
        summary_delete_text_after_summarize=summary_delete_text_after_summarize,
    )
    Config.save_telegram(
        enabled=tg_enabled,
        bot_token=tg_token,
        chat_id=tg_chat_id,
        auto_delete=tg_auto_delete,
    )

    console.print("  [bold green]설정이 저장되었습니다.[/bold green]")
    console.print()
    _print_summary(
        download_dir, download_rule, stt_enabled, ai_enabled, gemini_model if ai_enabled and api_key else "", tg_enabled
    )
    console.print()
    Prompt.ask("  [dim]Enter를 눌러 계속[/dim]", default="")


def _print_section(title: str) -> None:
    console.print(f"  [bold]{title}[/bold]")
    console.print()


def _print_summary(
    download_dir: str,
    download_rule: str,
    stt_enabled: bool,
    ai_enabled: bool,
    gemini_model: str,
    tg_enabled: bool = False,
) -> None:
    """설정 요약을 표시한다."""
    rule_label = {"mp4": "영상만 (mp4)", "mp3": "음성만 (mp3)", "both": "영상 + 음성"}.get(download_rule, download_rule)
    console.print("  [dim]─────────────────────────────[/dim]")
    console.print(f"  다운로드 경로  : [cyan]{download_dir}[/cyan]")
    console.print(f"  다운로드 규칙  : [cyan]{rule_label}[/cyan]")
    if download_rule != "mp4":
        console.print(f"  STT 변환      : [cyan]{'사용' if stt_enabled else '미사용'}[/cyan]")
    console.print(f"  AI 요약       : [cyan]{'사용' if ai_enabled else '미사용'}[/cyan]")
    if ai_enabled and gemini_model:
        console.print(f"  Gemini 모델   : [cyan]{gemini_model}[/cyan]")
    console.print(f"  텔레그램 알림  : [cyan]{'사용' if tg_enabled else '미사용'}[/cyan]")
    console.print("  [dim]─────────────────────────────[/dim]")
