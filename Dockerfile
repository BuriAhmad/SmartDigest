FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

COPY requirements.txt ./

RUN python -m venv "${VIRTUAL_ENV}" \
    && pip install --upgrade pip \
    && pip install --no-compile -r requirements.txt \
    && pip check


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN groupadd --system --gid 10001 smartdigest \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/smartdigest smartdigest

COPY --from=builder /opt/venv /opt/venv
COPY --chown=smartdigest:smartdigest app ./app
COPY --chown=smartdigest:smartdigest alembic ./alembic
COPY --chown=smartdigest:smartdigest templates ./templates
COPY --chown=smartdigest:smartdigest worker.py ./worker.py
COPY --chown=smartdigest:smartdigest alembic.ini ./alembic.ini
COPY --chown=smartdigest:smartdigest scripts/run_release_tasks.sh ./scripts/run_release_tasks.sh

USER smartdigest

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
