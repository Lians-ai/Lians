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
COPY pyproject.toml ./
COPY agentmem/src/ ./agentmem/src/
RUN pip install --no-cache-dir -e .

# Copy the remaining source (migrations, SDK, examples)
COPY agentmem/ ./agentmem/

# Run from agentmem/ so that:
#   - alembic finds alembic.ini in the CWD
#   - uvicorn resolves src.agentmem.main via the CWD on sys.path
WORKDIR /app/agentmem

EXPOSE 8000

CMD ["uvicorn", "src.agentmem.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
