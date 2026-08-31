FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* ./

RUN pip install --no-cache-dir --upgrade pip wheel setuptools

RUN uv sync --frozen --no-dev

# Chrome 우선, ARM64 등 미지원 시 Chromium fallback
RUN uv run playwright install --with-deps chrome 2>/dev/null \
    || uv run playwright install --with-deps chromium

COPY src/ ./src/
COPY backend/ ./backend/
COPY CHANGELOG.md ./

RUN mkdir -p /downloads \
    && mkdir -p /db \
    && mkdir -p /root/.cache/huggingface \
    && mkdir -p /root/.cache/ms-playwright

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# CMD는 docker-compose의 command:로 오버라이드 가능
# 개발: command: backend.main:app --host 0.0.0.0 --port 8000 --reload
# 프로덕션: command: backend.main:app --host 0.0.0.0 --port 8000 --workers 4
ENTRYPOINT ["uv", "run", "--no-sync", "uvicorn"]
CMD ["backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
