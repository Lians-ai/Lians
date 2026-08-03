FROM postgres:16.14-bookworm@sha256:92620daddcd947f8d5ab5ba66e848702fe443d87fed30c4cea8e389fd78dfc55

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 liansbackup \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin liansbackup

WORKDIR /opt/lians-backup
COPY --chown=root:root . ./
RUN chmod -R a=rX,u+w /opt/lians-backup

USER 10001:10001
ENTRYPOINT []
CMD ["python3", "/opt/lians-backup/create_logical_backup.py", "--help"]
