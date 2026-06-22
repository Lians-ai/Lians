FROM python:3.12-slim

WORKDIR /app

# System deps: gcc + libpq-dev for asyncpg C extension; libffi-dev for cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying the full source tree so that
# Docker reuses this layer on code-only changes (pyproject.toml is the cache key).
# [local] installs sentence-transformers for self-hosted, air-gapped deployments.
# Pass --build-arg EXTRAS= to build a leaner image when using EMBEDDING_PROVIDER=voyage.
ARG EXTRAS=local
COPY pyproject.toml ./
COPY agentmem/src/ ./agentmem/src/
RUN pip install --no-cache-dir -e ".[$EXTRAS]"

# Pre-download the sentence-transformers model at build time so:
#   (a) first startup requires zero network access
#   (b) runtime works with readOnlyRootFilesystem: true (no downloads at startup)
# The model lands in /app/.model_cache (set via SENTENCE_TRANSFORMERS_HOME below).
# Pass --build-arg PREDOWNLOAD_MODEL= to skip the download (e.g. CI, non-local provider).
ARG PREDOWNLOAD_MODEL=BAAI/bge-large-en-v1.5
ENV SENTENCE_TRANSFORMERS_HOME=/app/.model_cache
RUN if [ -n "$PREDOWNLOAD_MODEL" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; \
                 SentenceTransformer('$PREDOWNLOAD_MODEL')"; \
    fi

# After baking the model in, tell HuggingFace libraries not to check for updates
# or attempt any network calls. This enforces true air-gap behaviour at runtime.
ENV TRANSFORMERS_OFFLINE=1
ENV HF_DATASETS_OFFLINE=1

# Copy the remaining source (migrations, SDK, examples)
COPY agentmem/ ./agentmem/

# Run from agentmem/ so that:
#   - alembic finds alembic.ini in the CWD
#   - uvicorn resolves src.agentmem.main via the CWD on sys.path
WORKDIR /app/agentmem

EXPOSE 8000

CMD ["uvicorn", "src.agentmem.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
