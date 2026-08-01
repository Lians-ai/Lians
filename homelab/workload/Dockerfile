FROM python:3.12-alpine

ARG WORKLOAD_UID=1000
ARG WORKLOAD_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY common.py scenario.py dataset.py bulk_ingest.py bootstrap.py runner.py verify.py ./

RUN pip install --no-cache-dir "cryptography==48.0.1"

RUN mkdir -p /state \
    && chown -R "${WORKLOAD_UID}:${WORKLOAD_GID}" /app /state

USER ${WORKLOAD_UID}:${WORKLOAD_GID}

CMD ["python", "runner.py"]
