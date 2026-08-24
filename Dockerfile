FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder

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
# Keep this aligned with the public torch release selected by the local extra;
# the official PyTorch index publishes its Linux CPU build with the +cpu tag.
ARG TORCH_CPU_VERSION=2.13.0+cpu
ARG PYTORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
COPY pyproject.toml ./
COPY agentmem/src/ ./agentmem/src/
RUN python -m pip install --no-cache-dir --upgrade pip==25.3 \
    && if [ -n "$EXTRAS" ]; then package_spec=".[$EXTRAS]"; else package_spec="."; fi \
    && case ",$EXTRAS," in \
      *,local,*) \
        python -m pip install --no-cache-dir \
          --index-url "$PYTORCH_CPU_INDEX_URL" \
          "torch==$TORCH_CPU_VERSION" \
        && printf 'torch==%s\n' "$TORCH_CPU_VERSION" > /tmp/torch-cpu-constraints.txt \
        && python -m pip install --no-cache-dir \
          --constraint /tmp/torch-cpu-constraints.txt \
          "$package_spec" \
        ;; \
      *) python -m pip install --no-cache-dir "$package_spec" ;; \
    esac \
    && python -m pip check \
    && case ",$EXTRAS," in \
      *,local,*) \
        TORCH_CPU_VERSION="$TORCH_CPU_VERSION" python -c \
          "import os; from importlib import metadata; import torch; names = {d.metadata['Name'].lower() for d in metadata.distributions()}; banned = sorted(n for n in names if n.startswith(('nvidia-', 'cuda-')) or n == 'triton'); expected = os.environ['TORCH_CPU_VERSION']; assert metadata.version('torch') == expected, (metadata.version('torch'), expected); assert torch.version.cuda is None, torch.version.cuda; assert not banned, banned" \
        ;; \
    esac

# Pre-download the local embedding model for zero-network runtime startup.
ARG PREDOWNLOAD_MODEL=BAAI/bge-large-en-v1.5
ARG PREDOWNLOAD_MODEL_REVISION=d4aa6901d3a41ba39fb536a557fa166f842b0e09
ENV SENTENCE_TRANSFORMERS_HOME=/app/.model_cache
RUN mkdir -p "$SENTENCE_TRANSFORMERS_HOME" \
    && if [ -n "$PREDOWNLOAD_MODEL" ]; then \
      python -c "from sentence_transformers import SentenceTransformer; \
                 SentenceTransformer('$PREDOWNLOAD_MODEL', revision='$PREDOWNLOAD_MODEL_REVISION')"; \
    fi


FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

ARG PREDOWNLOAD_MODEL_REVISION=d4aa6901d3a41ba39fb536a557fa166f842b0e09

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 lians \
    && useradd --system --uid 10001 --gid lians \
       --create-home --home-dir /home/lians lians

ENV PATH="/opt/venv/bin:${PATH}" \
    SENTENCE_TRANSFORMERS_HOME=/app/.model_cache \
    SENTENCE_TRANSFORMER_REVISION="${PREDOWNLOAD_MODEL_REVISION}" \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set final ownership as each layer is materialized. A recursive chown here
# duplicates metadata for the multi-gigabyte environment and model layers.
COPY --from=builder --chown=10001:10001 /opt/venv /opt/venv
COPY --from=builder --chown=10001:10001 /app/.model_cache /app/.model_cache
# Runtime code is already installed in /opt/venv. Keep only the migration
# assets needed for an operator-controlled schema upgrade; tests, SDK sources,
# benchmarks, and local Compose files do not belong in the serving image.
COPY --chown=10001:10001 agentmem/alembic /app/agentmem/alembic
COPY --chown=10001:10001 agentmem/alembic.ini /app/agentmem/alembic.ini

ARG LIANS_VERSION=0.5.0
ARG LIANS_BUILD_SHA=unknown
ENV LIANS_BUILD_SHA="${LIANS_BUILD_SHA}" \
    LIANS_VERSION="${LIANS_VERSION}"

LABEL org.opencontainers.image.title="Lians Engine" \
      org.opencontainers.image.description="Trusted memory and evidence service for AI systems" \
      org.opencontainers.image.source="https://github.com/Lians-ai/Lians" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${LIANS_VERSION}" \
      org.opencontainers.image.revision="${LIANS_BUILD_SHA}"

WORKDIR /app/agentmem
USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "lians.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
