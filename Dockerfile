# syntax=docker/dockerfile:1

# --- builder: install dependencies into a venv with uv ---
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached unless pyproject/uv.lock change), then the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev


# --- runtime: slim image with just the venv + app ---
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY --from=builder /app /app

# Bake the embedding model into the image so first request is fast and works offline.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/data/models')"

# The `browse` tool needs Chromium (heavy). Off by default to keep the image lean;
# enable with:  docker build --build-arg INSTALL_CHROMIUM=true .
ARG INSTALL_CHROMIUM=false
RUN if [ "$INSTALL_CHROMIUM" = "true" ]; then playwright install --with-deps chromium; fi

EXPOSE 8000

# Apply migrations, then serve. (On Postgres the app relies on Alembic for schema.)
CMD ["sh", "-c", "alembic upgrade head && uvicorn oli.main:app --host 0.0.0.0 --port 8000"]
