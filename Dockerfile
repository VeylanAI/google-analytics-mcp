FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv

ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

ARG APP_USER=mcp
ARG APP_UID=1000
ARG APP_GID=1000

ENV HOME=/home/${APP_USER}

RUN python -m venv "${VIRTUAL_ENV}" \
    && groupadd --system --gid "${APP_GID}" "$APP_USER" \
    && useradd --system --uid "${APP_UID}" --gid "${APP_GID}" --home "${HOME}" --create-home "$APP_USER"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY analytics_mcp ./analytics_mcp

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .


RUN chown -R "${APP_USER}":"${APP_USER}" /app "${VIRTUAL_ENV}"

USER $APP_USER

CMD ["analytics-mcp"]