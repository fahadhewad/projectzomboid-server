# Project Zomboid dedicated server.
#
# Two-stage split on purpose: the game itself is NOT baked into the image. It is
# downloaded by SteamCMD into a volume at first boot and updated on every
# restart. Baking a ~3 GB game into an image would mean rebuilding the image for
# every patch, and redistributing game files we have no licence to redistribute.
# The image is the *runtime*; the volume is the *data*.

FROM debian:bookworm-slim

# SteamCMD is a 32-bit binary, so the i386 architecture has to be enabled even
# though the game server itself is 64-bit.
RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        lib32gcc-s1 \
        libsdl2-2.0-0 \
        locales \
        procps \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# PZ writes UTF-8 player names and mod titles; without a UTF-8 locale the JVM
# mangles them in logs and in the config file.
RUN sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen && locale-gen
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Run as an unprivileged user. A game server is internet-facing and parses
# untrusted input (mods, player data), so it has no business running as root.
ARG PUID=1000
ARG PGID=1000
RUN groupadd -g "${PGID}" pzserver \
    && useradd -u "${PUID}" -g "${PGID}" -m -d /home/pzserver pzserver \
    && mkdir -p /opt/steamcmd /opt/pzserver /data \
    && chown -R pzserver:pzserver /opt/steamcmd /opt/pzserver /data

USER pzserver
WORKDIR /opt/steamcmd

RUN curl -sSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz \
    | tar -xz -C /opt/steamcmd

# The pzops CLI renders the server config from environment variables at boot.
COPY --chown=pzserver:pzserver pyproject.toml /opt/pzops/pyproject.toml
COPY --chown=pzserver:pzserver src /opt/pzops/src
ENV PYTHONPATH=/opt/pzops/src

# Python is only needed for config rendering here, so the interpreter goes in
# without pip. Note this is python3, NOT python3-minimal: Debian's minimal
# package omits most of the standard library (argparse, tomllib, tarfile), so
# pzops would fail on import at boot.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 \
    && rm -rf /var/lib/apt/lists/*
COPY docker/entrypoint-server.sh /usr/local/bin/entrypoint-server.sh
# Strip CR before chmod. .gitattributes pins LF for checkouts, but a clone made
# before that existed still carries CRLF, and a CRLF shebang makes the kernel
# hunt for an interpreter named "bash\r" — exit 127 in a restart loop.
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint-server.sh \
    && chmod +x /usr/local/bin/entrypoint-server.sh
USER pzserver

COPY --chown=pzserver:pzserver config/templates /templates

# 16261 is the game port, 16262 the direct-connect/player channel. Both UDP.
EXPOSE 16261/udp 16262/udp 27015/tcp

# Two volumes, not one. /data is the world; /opt/pzserver is the game install
# plus every downloaded Workshop mod. Without the second one, the container's
# writable layer holds them, so any image rebuild discards ~3 GB of game and the
# entire mod set and re-downloads it all on next boot.
VOLUME ["/data", "/opt/pzserver"]
WORKDIR /opt/pzserver

# The JVM ignores SIGTERM's default disposition, so give it room to flush the
# world to disk. PZ saves on shutdown; killing it early is how saves corrupt.
STOPSIGNAL SIGTERM

# Liveness only: this says the server process is alive, not that players can
# connect. The start period is generous because the first boot downloads ~3 GB
# from Steam before the game process exists at all.
HEALTHCHECK --interval=60s --timeout=10s --start-period=600s --retries=3 \
    CMD pgrep -f "zombie.network.GameServer" > /dev/null \
        || pgrep -x java > /dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint-server.sh"]
