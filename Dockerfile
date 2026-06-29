FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/models/huggingface \
    TRANSFORMERS_CACHE=/opt/models/huggingface/transformers \
    SENTENCE_TRANSFORMERS_HOME=/opt/models/sentence-transformers \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

ARG SEMANTIC_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
ARG RERANKER_MODEL_NAME=cross-encoder/ettin-reranker-68m-v1
ARG DOWNLOAD_SEMANTIC_MODEL=true
ARG DOWNLOAD_RERANKER_MODEL=true

WORKDIR /build

COPY requirements.txt ./
COPY scripts/download_models.py ./scripts/download_models.py

RUN python -m venv "${VIRTUAL_ENV}" \
    && pip install --upgrade pip \
    && pip install --no-compile -r requirements.txt \
    && mkdir -p "${HF_HOME}" "${TRANSFORMERS_CACHE}" "${SENTENCE_TRANSFORMERS_HOME}" \
    && SEMANTIC_MODEL_NAME="${SEMANTIC_MODEL_NAME}" \
       RERANKER_MODEL_NAME="${RERANKER_MODEL_NAME}" \
       DOWNLOAD_SEMANTIC_MODEL="${DOWNLOAD_SEMANTIC_MODEL}" \
       DOWNLOAD_RERANKER_MODEL="${DOWNLOAD_RERANKER_MODEL}" \
       python scripts/download_models.py \
    && pip check


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/models/huggingface \
    TRANSFORMERS_CACHE=/opt/models/huggingface/transformers \
    SENTENCE_TRANSFORMERS_HOME=/opt/models/sentence-transformers \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PORT=8080 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

RUN groupadd --system --gid 10001 smartdigest \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/smartdigest smartdigest

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models
COPY --chown=smartdigest:smartdigest app ./app
COPY --chown=smartdigest:smartdigest alembic ./alembic
COPY --chown=smartdigest:smartdigest templates ./templates
COPY --chown=smartdigest:smartdigest worker.py ./worker.py
COPY --chown=smartdigest:smartdigest alembic.ini ./alembic.ini
COPY --chown=smartdigest:smartdigest scripts/run_release_tasks.sh ./scripts/run_release_tasks.sh

USER smartdigest

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
