# syntax=docker/dockerfile:1.7

FROM python:3.13-slim AS model-fetch
ENV HF_HOME=/opt/models/huggingface
RUN pip install --no-cache-dir huggingface-hub==0.36.0
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='BAAI/bge-m3', revision='5617a9f61b028005a4858fdac845db406aefb181', cache_dir='/opt/models/huggingface')"

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/models/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements/runtime.txt /tmp/requirements.txt
RUN pip install --no-cache-dir torch==2.12.0 --index-url https://download.pytorch.org/whl/cu130 \
    && pip install --no-cache-dir -r /tmp/requirements.txt
COPY --from=model-fetch /opt/models/huggingface /opt/models/huggingface
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir --no-deps .

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl --fail http://127.0.0.1:8080/health/ready || exit 1
CMD ["knowledge-api"]

