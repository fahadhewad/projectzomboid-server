#!/usr/bin/env bash
#
# Boot sequence for the game server container:
#   1. install or update the game via SteamCMD
#   2. render servertest.ini from environment variables
#   3. hand the container's PID 1 to the game server
#
# Every step is idempotent, so `docker compose restart` is always safe.

set -euo pipefail

STEAMCMD_DIR="${STEAMCMD_DIR:-/opt/steamcmd}"
SERVER_DIR="${SERVER_DIR:-/opt/pzserver}"
DATA_DIR="${DATA_DIR:-/data}"
TEMPLATE_DIR="${TEMPLATE_DIR:-/templates}"
SERVER_NAME="${PZ_SERVER_NAME:-servertest}"
STEAM_APP_ID=380870  # Project Zomboid Dedicated Server

log() { printf '%s [entrypoint] %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S')" "$*"; }

# ---------------------------------------------------------------------------
# 1. Install or update the game
# ---------------------------------------------------------------------------
if [ "${PZ_SKIP_UPDATE:-0}" = "1" ]; then
    log "PZ_SKIP_UPDATE=1, skipping SteamCMD"
else
    # Mod collections often target a specific build (e.g. Build 42), so the
    # branch has to be selectable. Empty = Steam's default/stable branch.
    APP_UPDATE_ARGS=("${STEAM_APP_ID}")
    if [ -n "${PZ_STEAM_BRANCH:-}" ]; then
        APP_UPDATE_ARGS+=(-beta "${PZ_STEAM_BRANCH}")
        if [ -n "${PZ_STEAM_BRANCH_PASSWORD:-}" ]; then
            APP_UPDATE_ARGS+=(-betapassword "${PZ_STEAM_BRANCH_PASSWORD}")
        fi
        log "using Steam branch '${PZ_STEAM_BRANCH}'"
    fi

    log "updating Project Zomboid dedicated server (app ${STEAM_APP_ID})"
    # `validate` repairs a partial download from an interrupted update, which is
    # the usual cause of a server that installs fine but won't boot.
    "${STEAMCMD_DIR}/steamcmd.sh" \
        +force_install_dir "${SERVER_DIR}" \
        +login anonymous \
        +app_update "${APP_UPDATE_ARGS[@]}" validate \
        +quit
    log "game files up to date"
fi

if [ ! -x "${SERVER_DIR}/start-server.sh" ]; then
    log "ERROR: ${SERVER_DIR}/start-server.sh missing - the download failed"
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Render the server config
# ---------------------------------------------------------------------------
# PZ keeps config next to the saves, both under the data volume, so the config
# survives an image rebuild. PZ names the file after the server, so render into
# a scratch directory first and place it under the final name — rendering
# straight into place would recreate servertest.ini on every boot once the file
# had been renamed, and .env edits would silently stop applying.
mkdir -p "${DATA_DIR}/Server"
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "${STAGE_DIR}"' EXIT

log "rendering server config into ${DATA_DIR}/Server"
python3 -m pzops render-config --templates "${TEMPLATE_DIR}" --dest "${STAGE_DIR}"

TARGET="${DATA_DIR}/Server/${SERVER_NAME}.ini"
if [ ! -f "${TARGET}" ]; then
    cp "${STAGE_DIR}/servertest.ini" "${TARGET}"
    log "created ${TARGET}"
elif [ "${PZ_CONFIG_OVERWRITE:-0}" = "1" ]; then
    cp "${STAGE_DIR}/servertest.ini" "${TARGET}"
    log "overwrote ${TARGET} from .env (PZ_CONFIG_OVERWRITE=1)"
else
    # Default: never clobber a file edited by hand or by an in-game admin.
    log "keeping existing ${TARGET} (set PZ_CONFIG_OVERWRITE=1 to regenerate)"
fi

# ---------------------------------------------------------------------------
# 3. Start the server
# ---------------------------------------------------------------------------
SERVER_ARGS=(-cachedir="${DATA_DIR}" -servername "${SERVER_NAME}")

# Supplying the admin password non-interactively matters: without it the server
# blocks on a prompt at first boot and the container looks hung forever.
if [ -n "${PZ_ADMIN_PASSWORD:-}" ]; then
    SERVER_ARGS+=(-adminusername "${PZ_ADMIN_USERNAME:-admin}"
                  -adminpassword "${PZ_ADMIN_PASSWORD}")
else
    log "WARNING: PZ_ADMIN_PASSWORD is unset; first boot may block on a prompt"
fi

if [ -n "${PZ_EXTRA_ARGS:-}" ]; then
    # Intentionally word-split: this is an argument list, not a single argument.
    # shellcheck disable=SC2206
    SERVER_ARGS+=(${PZ_EXTRA_ARGS})
fi

cd "${SERVER_DIR}"
log "starting server '${SERVER_NAME}'"

# exec replaces this shell, so the JVM becomes PID 1 and receives SIGTERM
# directly from `docker stop`. Without exec the signal would hit bash, bash
# would exit, and the game would be SIGKILLed mid-write with the world half
# saved. This one keyword is the difference between clean shutdowns and
# corrupted saves.
exec ./start-server.sh "${SERVER_ARGS[@]}"
