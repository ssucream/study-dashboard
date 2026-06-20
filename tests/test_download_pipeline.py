"""다운로드 파이프라인 STT 연결 테스트."""

from pathlib import Path

import pytest

from src.downloader import pipeline


@pytest.mark.asyncio
async def test_download_pipeline_transcribes_mp3_and_deletes_audio(monkeypatch, tmp_path):
    async def fake_extract_video_url(page, lecture_url):
        return "https://cdn.example/video.mp4"

    async def fake_download_video_with_browser(page, video_url, save_path, on_progress=None):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"mp4")
        if on_progress:
            on_progress(10, 10)

    def fake_convert_to_mp3(mp4_path: Path):
        mp3_path = mp4_path.with_suffix(".mp3")
        mp3_path.write_bytes(b"mp3")
        return mp3_path

    def fake_transcribe(audio_path: Path, model_size: str = "base", language: str = "", on_model_loaded=None):
        txt_path = audio_path.with_suffix(".txt")
        txt_path.write_text(f"{model_size}:{language}", encoding="utf-8")
        return txt_path

    monkeypatch.setattr(pipeline, "extract_video_url", fake_extract_video_url)
    monkeypatch.setattr(pipeline, "download_video_with_browser", fake_download_video_with_browser)
    monkeypatch.setattr(pipeline, "convert_to_mp3", fake_convert_to_mp3)
    monkeypatch.setattr("src.stt.transcriber.transcribe", fake_transcribe)

    result = await pipeline.download_lecture_media(
        page=object(),
        lecture_url="https://canvas.ssu.ac.kr/courses/1/items/1",
        lecture_title="1강",
        week_label="1주차",
        course_name="테스트",
        download_dir=str(tmp_path),
        rule="mp3",
        stt_enabled=True,
        stt_model="tiny",
        stt_language="ko",
        delete_audio_after_stt=True,
    )

    txt_path = Path(result["stt"]["txt_path"])
    mp3_path = Path(result["stt"]["audio_path"])
    assert txt_path.read_text(encoding="utf-8") == "tiny:ko"
    assert not mp3_path.exists()
    assert result["stt"]["audio_deleted"] is True
    assert any(file["type"] == "txt" for file in result["files"])
    assert any(file["type"] == "mp3" and file["deleted"] == "true" for file in result["files"])


@pytest.mark.asyncio
async def test_download_pipeline_summarizes_txt_and_deletes_source(monkeypatch, tmp_path):
    async def fake_extract_video_url(page, lecture_url):
        return "https://cdn.example/video.mp4"

    async def fake_download_video_with_browser(page, video_url, save_path, on_progress=None):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"mp4")

    def fake_convert_to_mp3(mp4_path: Path):
        mp3_path = mp4_path.with_suffix(".mp3")
        mp3_path.write_bytes(b"mp3")
        return mp3_path

    def fake_transcribe(audio_path: Path, model_size: str = "base", language: str = "", on_model_loaded=None):
        txt_path = audio_path.with_suffix(".txt")
        txt_path.write_text("강의 원문", encoding="utf-8")
        return txt_path

    def fake_summarize(
        txt_path: Path,
        agent: str,
        api_key: str,
        model: str,
        extra_prompt: str = "",
        course_name: str = "",
        prompt_template: str = "",
    ):
        summary_path = txt_path.with_stem(txt_path.stem + "_summarized")
        summary_path.write_text(f"{agent}:{model}:{extra_prompt}:{course_name}:{prompt_template}", encoding="utf-8")
        return summary_path

    monkeypatch.setattr(pipeline, "extract_video_url", fake_extract_video_url)
    monkeypatch.setattr(pipeline, "download_video_with_browser", fake_download_video_with_browser)
    monkeypatch.setattr(pipeline, "convert_to_mp3", fake_convert_to_mp3)
    monkeypatch.setattr("src.stt.transcriber.transcribe", fake_transcribe)
    monkeypatch.setattr("src.summarizer.summarizer.summarize", fake_summarize)

    result = await pipeline.download_lecture_media(
        page=object(),
        lecture_url="https://canvas.ssu.ac.kr/courses/1/items/1",
        lecture_title="1강",
        week_label="1주차",
        course_name="테스트",
        download_dir=str(tmp_path),
        rule="both",
        stt_enabled=True,
        ai_enabled=True,
        ai_agent="gemini",
        ai_api_key="key",
        ai_model="gemini-2.5-flash",
        summary_prompt_template="프롬프트 {text}",
        summary_prompt_extra="시험 대비",
        delete_text_after_summary=True,
    )

    txt_path = Path(result["summary"]["txt_path"])
    summary_path = Path(result["summary"]["summary_path"])
    assert not txt_path.exists()
    assert summary_path.read_text(encoding="utf-8") == "gemini:gemini-2.5-flash:시험 대비:테스트:프롬프트 {text}"
    assert result["summary"]["text_deleted"] is True
    assert any(file["type"] == "summary" for file in result["files"])
    assert any(file["type"] == "txt" and file["deleted"] == "true" for file in result["files"])


@pytest.mark.asyncio
async def test_download_pipeline_does_not_transcribe_mp4_rule(monkeypatch, tmp_path):
    async def fake_extract_video_url(page, lecture_url):
        return "https://cdn.example/video.mp4"

    async def fake_download_video_with_browser(page, video_url, save_path, on_progress=None):
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(b"mp4")

    monkeypatch.setattr(pipeline, "extract_video_url", fake_extract_video_url)
    monkeypatch.setattr(pipeline, "download_video_with_browser", fake_download_video_with_browser)

    result = await pipeline.download_lecture_media(
        page=object(),
        lecture_url="https://canvas.ssu.ac.kr/courses/1/items/1",
        lecture_title="1강",
        week_label="1주차",
        course_name="테스트",
        download_dir=str(tmp_path),
        rule="mp4",
        stt_enabled=True,
    )

    assert result["stt"] == {"enabled": False}
    assert all(file["type"] != "txt" for file in result["files"])
