"""예상 문제 생성·채점 도메인 로직.

강의 요약본 / STT 원본 / 업로드 강의자료를 근거로 시험 예상 문제를 생성하고,
사용자 답안을 채점한다. AI 호출 부분은 src/summarizer/summarizer.py의 provider
분기 패턴을 재사용한다.
"""
