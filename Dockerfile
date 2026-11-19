# syntax=docker/dockerfile:1
# 배포용 API 이미지. 같은 이미지로 마이그레이션(alembic)과 대회 수집(app.contests.collect)도 돌린다.
# 비밀 값은 이미지에 넣지 않고 실행 때 환경변수로 준다 (AGENTS.md 하드 규칙, .env 는 .dockerignore 로 제외).

# ── 1단계: 잠금 파일(uv.lock) 그대로 운영 의존성만 가상환경에 설치 ──
FROM python:3.13-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.8.15 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# ── 2단계: uv·빌드 캐시 없이 가상환경과 소스만 담은 실행 이미지 ──
FROM python:3.13-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
