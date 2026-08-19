# Backup sidecar.
#
# Deliberately tiny and separate from the game server. If the backup job dies at
# 3am, nobody gets kicked off the server — the two containers fail independently.
# It also means this image contains rclone and a Python runtime, while the game
# image contains neither.

FROM python:3.11-slim-bookworm

# rclone is a single static binary from Debian's archive; it is the only reason
# this image needs anything beyond Python.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rclone ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

ARG PUID=1000
ARG PGID=1000
RUN groupadd -g "${PGID}" pzops \
    && useradd -u "${PUID}" -g "${PGID}" -m -d /home/pzops pzops

WORKDIR /opt/pzops
COPY pyproject.toml ./
COPY src ./src

# --no-deps is honest here rather than lazy: pzops has zero runtime
# dependencies, so there is nothing to resolve and no wheel cache to bloat.
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /backups && chown -R pzops:pzops /backups /home/pzops
USER pzops

# Buffered logs are invisible logs: without this, `docker compose logs -f` shows
# nothing until the buffer flushes, which looks exactly like a hung container.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["pzops"]
CMD ["backup", "--daemon"]
