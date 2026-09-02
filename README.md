# study-dashboard

숭실대학교 Learning X(canvas.ssu.ac.kr) 강의 영상을 자동으로 재생·변환·요약하여
웹 대시보드에서 학기 / 과목 / 주차별 마크다운 형식으로 열람할 수 있는 개인 학습 서비스입니다.

---

## 화면 흐름

```
로그인 (Learning X 계정)
  └── 메인 대시보드
        ├── [자동 모드 ON/OFF 토글]
        │     ├── (ON)  현재 재생 중인 강의 상태 표시
        │     └── (ON)  다음 실행 스케줄 표시
        └── 학기 선택  (예: 2026년 1학기 / 2026년 2학기)
              └── 과목 목록
                    └── 주차별 강의 요약  (마크다운 렌더링)
```

---

## 자동화 파이프라인

```
강의 영상 백그라운드 재생 → Whisper STT → AI 요약 → 마크다운 저장 → 웹 열람
```

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| LMS 연동 로그인 | Learning X 계정(학번/비밀번호)으로 인증 |
| 학기·과목·강의 스크래핑 | 로그인 후 전체 강의 목록 자동 수집 |
| 백그라운드 재생 | 영상·소리 출력 없이 강의 자동 재생 (출석 처리) |
| 로컬 STT | faster-whisper로 오프라인 음성 텍스트 변환 |
| AI 요약 | AI API로 강의 내용 자동 요약 |
| 마크다운 대시보드 | 학기 → 과목 → 주차 계층으로 요약 열람 |
| 자동 모드 | 스케줄에 따라 미시청 강의를 자동으로 재생·변환·요약 |
| 실시간 상태 표시 | 현재 재생 중인 강의 및 다음 스케줄을 대시보드에 표시 |

---

## 메인 대시보드

로그인 후 표시되는 메인 페이지에서 다음 정보를 확인하고 제어할 수 있습니다.

### 자동 모드 토글

| 상태 | 동작 |
|------|------|
| OFF | 자동 실행 없음. 학기·과목 탐색 및 요약 열람만 가능 |
| ON | 설정된 스케줄에 따라 미시청 강의 자동 처리 시작 |

### 자동 모드 ON 시 표시 정보

- **현재 재생 중인 강의** — 과목명, 강의명, 파이프라인 단계 (`재생 중` / `STT 변환 중` / `요약 중`)
- **다음 스케줄** — 다음 자동 실행 예정 시각 및 처리 대상 강의 수

> 자동 모드 스케줄 기본값: KST 09:00 / 13:00 / 18:00 / 23:00

---

## 기술 스택

### Backend
![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white) ![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=flat-square&logo=playwright&logoColor=white) ![faster-whisper](https://img.shields.io/badge/faster--whisper-412991?style=flat-square&logo=openai&logoColor=white)

### AI 요약 (선택형)
![Google Gemini](https://img.shields.io/badge/Google_Gemini-8E75B2?style=flat-square&logo=googlegemini&logoColor=white) ![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=flat-square&logo=openai&logoColor=white) ![OpenRouter](https://img.shields.io/badge/OpenRouter-6467F2?style=flat-square&logo=openrouter&logoColor=white)

> `AI_AGENT` 설정으로 Gemini / OpenAI / OpenRouter 중 하나를 선택합니다. API 키는 암호화되어 DB에 저장됩니다.

### Frontend
![HTML5](https://img.shields.io/badge/HTML5-E34F26?style=flat-square&logo=html5&logoColor=white) ![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?style=flat-square&logo=javascript&logoColor=black) ![Tailwind CSS](https://img.shields.io/badge/Tailwind_CSS-06B6D4?style=flat-square&logo=tailwindcss&logoColor=white)

### Infrastructure
![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat-square&logo=docker&logoColor=white) ![ffmpeg](https://img.shields.io/badge/ffmpeg-007808?style=flat-square&logo=ffmpeg&logoColor=white)

---

## 프로젝트 구조

```
study-dashboard/
├── backend/
│   ├── api/              # REST / WebSocket 라우터
│   ├── core/             # 설정, 인증, 암호화
│   ├── scraper/          # 과목·강의 스크래핑
│   ├── player/           # 백그라운드 재생
│   ├── converter/        # mp4 → mp3 (ffmpeg)
│   ├── stt/              # faster-whisper STT
│   └── summarizer/       # AI 요약
├── frontend/
│   └── src/
│       ├── pages/        # Login, Semesters, Courses, Summary
│       └── components/
├── data/
│   └── summaries/        # 마크다운 요약 파일 저장 경로
└── docker-compose.yml
```

---

## 시작 전 필요한 것

| 항목 | 설명 |
|------|------|
| 숭실대 Learning X 계정 | 학번 + 비밀번호 |
| Docker | 컨테이너 실행 환경 (Docker Desktop 등) |
| OpenSSL | 로컬 HTTPS 인증서 생성용 (대부분 OS에 기본 설치) |
| AI 요약 API 키 | AI 요약 사용 시 필요 (Gemini / OpenAI / OpenRouter) |

---

## 설치 및 실행

미리 빌드된 이미지를 Docker Hub에서 받아 실행합니다. **저장소를 클론할 필요가 없습니다.**
릴리즈에 첨부된 `docker-compose.yml` 한 파일만 있으면 됩니다.

### 1. 작업 폴더 생성 + compose 파일 다운로드

```bash
mkdir study-dashboard && cd study-dashboard

curl -LO https://github.com/ssucream/study-dashboard/releases/latest/download/docker-compose.yml
```

> `curl`이 없으면 [최신 릴리즈 페이지](https://github.com/ssucream/study-dashboard/releases/latest)에서
> `docker-compose.yml`을 직접 내려받아 이 폴더에 두면 됩니다.

### 2. 로컬 HTTPS 인증서 생성 (최초 1회)

브라우저 접속용 자체 서명 인증서를 만듭니다.

```bash
mkdir -p certs
openssl req -x509 -nodes -newkey rsa:2048 -sha256 -days 825 \
  -keyout certs/local.key -out certs/local.crt \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

### 3. 실행

```bash
docker compose pull
docker compose up -d
```

### 4. 접속

브라우저에서 **<https://localhost:3443>** 으로 접속합니다.
자체 서명 인증서라 브라우저가 경고를 띄우므로 "고급 → 계속 진행"으로 넘어갑니다.

Learning X 계정으로 로그인한 뒤, 계정·AI 요약 API 키·다운로드/STT/텔레그램 옵션은
웹 대시보드의 **설정** 화면에서 저장합니다.

> `http://localhost:3000` 으로 접속하면 자동으로 HTTPS(`3443`)로 리다이렉트됩니다.
> 백엔드 API(`127.0.0.1:8000`)는 로컬 헬스체크용으로만 노출되며 브라우저 트래픽은 nginx HTTPS를 거칩니다.

설정값·다운로드 파일·암호화 키·인증서는 실행 폴더의
`db/`, `downloads/`, `logs/`, `.secret_key`, `certs/` 에 저장되어
컨테이너를 내렸다 올려도 보존됩니다.

### 업데이트 / 중지

```bash
docker compose pull && docker compose up -d   # 최신 버전으로 업데이트
docker compose down                           # 중지 (데이터 보존)
docker compose down -v                         # 중지 + 캐시 볼륨까지 제거
```

---

## 개발자용 — 소스 빌드로 실행

소스를 직접 수정하며 개발할 때는 저장소를 클론한 뒤, `docker-compose.yml`의 `image:` 줄을
지우고 바로 아래 주석 처리된 `build:` 섹션(및 소스 볼륨 마운트)의 주석을 해제합니다.

```bash
git clone https://github.com/ssucream/study-dashboard.git
cd study-dashboard
./scripts/generate-local-cert.sh          # HTTPS 인증서 생성
# docker-compose.yml에서 image: → build: 로 교체하고 소스 볼륨 마운트 주석 해제 후
docker compose up --build
```

접속 주소는 배포용과 동일하게 **<https://localhost:3443>** 입니다.

---

## 설정 저장소

이 프로젝트는 `.env` 파일을 사용하지 않습니다. 설정은 SQLite DB(`db/app.db`)에 저장됩니다.
학번/비밀번호는 자동 로그인 방지를 위해 DB에 저장하지 않고 현재 세션 메모리에만 유지하며,
API 키/텔레그램 토큰은 암호화되어 DB에 저장됩니다.

Docker 실행 시 다운로드 경로는 컨테이너 `/downloads`로 고정됩니다.
기본 compose 설정은 실행 폴더의 `./downloads` 폴더를 컨테이너 `/downloads`에 마운트합니다.

### Whisper 모델 크기

faster-whisper는 INT8 양자화를 적용하므로 openai-whisper 대비 모델 파일 크기가 약 절반입니다.

| 모델 | 크기 (INT8) | 정확도 |
|------|------------|--------|
| tiny | ~39MB | 낮음 |
| base | ~74MB | 보통 (기본값) |
| small | ~122MB | 좋음 |
| medium | ~385MB | 높음 |
| large | ~750MB | 최고 |

---

## 개발 참고

Learning X 구조 분석, 재생/다운로드 구현 방식, 셀렉터 정의 등 기술 문서는 아래를 참고하세요.

- [Learning X 구조 분석 정의서](docs/lms-analysis.md)

---

## 주의사항

- 본 서비스는 개인 학습 목적으로만 사용하세요.
- Learning X 서비스 약관을 준수하여 사용하시기 바랍니다.
- 학번, 비밀번호는 현재 로그인 세션 메모리에만 유지되며 DB에 저장되지 않습니다.
- `.secret_key`와 `data/app.db`는 절대 외부에 공유하지 마세요.

### 면책 조항

본 프로젝트는 개인 학습 편의를 위해 제작된 비공식 도구입니다.

- 본 프로젝트의 사용으로 인해 발생하는 학사 불이익, 계정 제재, 데이터 손실 등 모든 결과에 대한 책임은 전적으로 사용자 본인에게 있습니다.
- 개발자는 어떠한 법적·도의적 책임도 지지 않습니다.
- 본 프로젝트는 [Claude AI](https://claude.ai)를 활용하여 개발되었습니다.
