FROM python:3.12-slim AS builder

WORKDIR /app

# Compilers and development headers exist only in the build stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Install a wheel-style package into an isolated virtual environment. Avoid an
# editable install whose .pth file would point back into the builder filesystem.
ARG EXTRAS=local
COPY pyproject.toml ./
COPY agentmem/src/ ./agentmem/src/
RUN python -m pip install --no-cache-dir --upgrade pip==25.3 \
    && if [ -n "$EXTRAS" ]; then package_spec=".[$EXTRAS]"; else package_spec="."; fi \
    && python -m pip install --no-cache-dir "$package_spec"

# Pre-download the local embedding model for zero-network runtime startup.
ARG PREDOWNLOAD_MODEL=BAAI/bge-large-en-v1.5
ENV SENTENCE_TRANSFORMERS_HOME=/app/.model_cache
RUN mkdir -p "$SENTENCE_TRANSFORMERS_HOME" \
    && if [ -n "$PREDOWNLOAD_MODEL" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; \
                 SentenceTransformer('$PREDOWNLOAD_MODEL')"; \
    fi


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 lians \
    && useradd --system --uid 10001 --gid lians \
       --create-home --home-dir /home/lians lians

ENV PATH="/opt/venv/bin:${PATH}" \
    SENTENCE_TRANSFORMERS_HOME=/app/.model_cache \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/.model_cache /app/.model_cache
COPY agentmem/ /app/agentmem/

RUN chown -R lians:lians /app /opt/venv /home/lians

ARG LIANS_BUILD_SHA=unknown
ENV LIANS_BUILD_SHA="${LIANS_BUILD_SHA}"

WORKDIR /app/agentmem
USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "lians.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
