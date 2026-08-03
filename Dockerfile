# syntax=docker/dockerfile:1.7

# Both build stages use an immutable, multi-architecture Python manifest.
FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

# Copy a release-pinned uv binary from its signed official image. The digest is
# the multi-architecture 0.11.29 index, so amd64 and arm64 resolve consistently.
COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

# Resolve nothing during the image build: uv.lock contains exact versions,
# artifact URLs, sizes, and SHA-256 hashes for every supported platform.
COPY pyproject.toml uv.lock build-constraints.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --extra production --no-install-project

# Build the project wheel with a second hash-verified lock dedicated to the
# isolated PEP 517 toolchain, then install it without resolving dependencies.
COPY agentmem/src/ ./agentmem/src/
COPY specs/ ./specs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel \
      --build-constraint build-constraints.lock \
      --require-hashes \
      --out-dir /tmp/lians-wheel . \
    && uv pip install --python /app/.venv/bin/python --no-deps /tmp/lians-wheel/*.whl

# Bake the default local embedding model into the release image for an air-gap
# capable startup. Set PREDOWNLOAD_MODEL= only for an explicitly external-only
# embedding image; the chosen build argument is recorded in provenance.
ARG PREDOWNLOAD_MODEL=Snowflake/snowflake-arctic-embed-l-v2.0
ARG PREDOWNLOAD_MODEL_REVISION=ac6544c8a46e00af67e330e85a9028c66b8cfd9a
ENV SENTENCE_TRANSFORMERS_HOME=/app/.model_cache
RUN mkdir -p /app/.model_cache \
    && if [ -n "$PREDOWNLOAD_MODEL" ]; then \
      /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('$PREDOWNLOAD_MODEL', revision='$PREDOWNLOAD_MODEL_REVISION', trust_remote_code=False)"; \
    fi


FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 lians \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin lians

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/.model_cache /app/.model_cache

# Runtime needs only Alembic's environment and revisions outside the installed
# wheel. Application code and public schemas are already in the locked venv.
COPY --chown=10001:10001 agentmem/alembic.ini ./agentmem/alembic.ini
COPY --chown=10001:10001 agentmem/alembic/ ./agentmem/alembic/

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SENTENCE_TRANSFORMERS_HOME=/app/.model_cache \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app/agentmem
USER 10001:10001
EXPOSE 8000

# One process per container prevents duplicate in-memory embedding models and
# lets the Helm/Fly/Render scheduler own horizontal scaling and drain semantics.
CMD ["/bin/sh", "-c", "exec uvicorn lians.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --no-server-header --no-proxy-headers"]
