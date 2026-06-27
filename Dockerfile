# Base application image for Streamlit invoice app plus bundled LLMOps runtime assets.

ARG PYTHON_BASE_IMAGE=python:3.11-slim
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:/root/.local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl nginx \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY llmops ./llmops
COPY scripts ./scripts
COPY prompts ./prompts
COPY schemas ./schemas
COPY data ./data
COPY docker/cloudrun/app/default.conf.template /etc/nginx/default.conf.template
COPY docker/cloudrun/app/entrypoint.sh /cloudrun-app-entrypoint.sh
COPY app_backend.py app_frontend.py app_frontend_helpers.py ./
COPY configs ./configs
COPY models/easyocr /models/easyocr

RUN uv sync --locked --no-dev
RUN sed -i 's/\r$//' /cloudrun-app-entrypoint.sh \
    && chmod +x /cloudrun-app-entrypoint.sh

RUN mkdir -p /app/outputs /models/easyocr /models/qwen

EXPOSE 8501 9108

ENTRYPOINT ["/bin/sh", "/cloudrun-app-entrypoint.sh"]
