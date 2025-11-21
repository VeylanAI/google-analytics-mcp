FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ARG APP_USER=mcp
ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_CACHE_DIR=/opt/uv-cache \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python

WORKDIR /app


RUN groupadd --system --gid "${APP_GID}" "$APP_USER" \
    && useradd --system --uid "${APP_UID}" --gid "${APP_GID}" --create-home "$APP_USER" \
    && mkdir -p "${UV_CACHE_DIR}" "${UV_PYTHON_INSTALL_DIR}" \
    && chown -R "${APP_UID}:${APP_GID}" "${UV_CACHE_DIR}" "${UV_PYTHON_INSTALL_DIR}"

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
    && chown -R "${APP_UID}:${APP_GID}" /app "${UV_CACHE_DIR}" "${UV_PYTHON_INSTALL_DIR}"

ENV PATH="/app/.venv/bin:${PATH}"

USER ${APP_USER}

CMD ["/app/.venv/bin/analytics-mcp"]