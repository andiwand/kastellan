# syntax=docker/dockerfile:1
# Kastellan in one image: the Python hub with the web app inside it.
# Build: docker build -t kastellan .   Run: see compose.yaml.

# 1. The web app.
FROM node:24-alpine AS web
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. The Python package, with the web app inside it, in a virtual environment.
FROM python:3.13-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
COPY --from=web /src/build ./src/kastellan/web
RUN uv sync --locked --no-dev --no-editable

# 3. The runtime.
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY --from=app /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    KASTELLAN_CONFIG=/config/config.yaml \
    KASTELLAN_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/config", "/data"]
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/api/health', timeout=4)"
CMD ["kastellan"]
