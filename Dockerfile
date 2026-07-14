# syntax=docker/dockerfile:1

########################################################################
# Stage 1: resolve & install production dependencies with uv.
# uv itself never ends up in the final runtime image.
########################################################################
FROM ghcr.io/astral-sh/uv:0.9-python3.13-bookworm-slim AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Only copy dependency manifests first so this layer is cached independently
# of application code changes.
COPY pyproject.toml uv.lock ./

# Install only the runtime dependency set (no dev tools such as pytest/ruff),
# and skip installing the project itself since it has no build backend and is
# only ever executed in place as "python -m src.updateDynDns".
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src

########################################################################
# Stage 2: minimal runtime image - no uv, no compilers, no dev tools.
########################################################################
FROM python:3.13-slim-bookworm AS runtime

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

# Bring in only the resolved virtual environment and application code from
# the builder stage.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src ./src
COPY docker-entrypoint.sh ./docker-entrypoint.sh

RUN chmod +x ./docker-entrypoint.sh \
    && mkdir -p /app/.temp \
    && chown -R app:app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

ENTRYPOINT ["./docker-entrypoint.sh"]
