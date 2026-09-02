# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

# --- deps layer: dependencies install without the source, so editing code
# doesn't invalidate the (slow) dependency layer ---
FROM base AS deps
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

# --- build layer: install the project itself into the venv ---
# Without this the venv holds dependencies but not webhook_service, and the
# container starts only to die on `ModuleNotFoundError: webhook_service`.
# It's a separate stage so the dependency cache above still does its job.
FROM deps AS build
WORKDIR /app
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev || uv sync --no-dev

# --- final runtime image ---
FROM base AS runtime
RUN groupadd -r app && useradd -r -g app app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src ./src
COPY pyproject.toml ./
# Migrations ship with the image so a deploy can run `alembic upgrade head`
# against the exact revisions that match this build.
COPY alembic.ini ./
COPY alembic ./alembic
ENV PATH="/app/.venv/bin:$PATH"

# No secrets are baked in: every credential arrives at runtime from the
# environment (in Kubernetes, from the Secret referenced by `secretRef`).
# There is deliberately no COPY of .env here, and .dockerignore excludes it
# so it cannot be pulled in by accident.
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

CMD ["uvicorn", "webhook_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
