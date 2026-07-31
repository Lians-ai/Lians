FROM python:3.12-alpine

ARG WORKLOAD_UID=1000
ARG WORKLOAD_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY common.py bootstrap.py runner.py verify.py ./

RUN mkdir -p /state \
    && chown -R "${WORKLOAD_UID}:${WORKLOAD_GID}" /app /state

USER ${WORKLOAD_UID}:${WORKLOAD_GID}

CMD ["python", "runner.py"]
