#!/usr/bin/env bash
#
# Verifies the entrypoint turns SIGTERM into an RCON quit.
#
# This exists because the original entrypoint used `exec`, which delivers
# SIGTERM to the JVM correctly but achieves nothing - PZ ignores it, so Docker
# SIGKILLed the server at the end of the stop grace period (exit code 137) on
# every single stop. The bug was invisible without a test like this.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

mkdir -p "${WORK}/server" "${WORK}/data" "${WORK}/templates" "${WORK}/bin"

# A stand-in for the game: ignores SIGTERM, exactly like the real thing.
cat > "${WORK}/server/start-server.sh" <<'EOF'
#!/usr/bin/env bash
trap '' TERM
echo "fake-pz: running" >&2
while true; do sleep 0.2; done
EOF
chmod +x "${WORK}/server/start-server.sh"

# A stand-in for pzops: renders a config, and on `rcon quit` kills the game the
# way a real RCON quit would.
cat > "${WORK}/bin/python3" <<EOF
#!/usr/bin/env bash
case "\$*" in
  *render-config*)
      for ((i=1; i<=\$#; i++)); do
          if [ "\${!i}" = "--dest" ]; then j=\$((i+1)); dest="\${!j}"; fi
      done
      mkdir -p "\${dest}"; echo "rendered=1" > "\${dest}/servertest.ini" ;;
  *"rcon quit"*)
      echo "RCON_QUIT_CALLED" >> "${WORK}/calls.log"
      pkill -KILL -f 'fake-pz|start-server.sh' 2>/dev/null || true ;;
esac
exit 0
EOF
chmod +x "${WORK}/bin/python3"

export PATH="${WORK}/bin:${PATH}"
export PZ_SKIP_UPDATE=1 SERVER_DIR="${WORK}/server" DATA_DIR="${WORK}/data"
export TEMPLATE_DIR="${WORK}/templates" PZ_RCON_PASSWORD=testpw
export PZ_ADMIN_PASSWORD=adminpw PZ_SHUTDOWN_WAIT=10

bash "${ROOT}/docker/entrypoint-server.sh" > "${WORK}/out.log" 2>&1 &
WRAPPER=$!

for _ in $(seq 1 50); do
    grep -q "starting server" "${WORK}/out.log" 2>/dev/null && break
    sleep 0.2
done
grep -q "starting server" "${WORK}/out.log" || { echo "FAIL: never started"; cat "${WORK}/out.log"; exit 1; }

START=$(date +%s)
kill -TERM "${WRAPPER}"

for _ in $(seq 1 100); do
    kill -0 "${WRAPPER}" 2>/dev/null || break
    sleep 0.2
done
ELAPSED=$(( $(date +%s) - START ))

if kill -0 "${WRAPPER}" 2>/dev/null; then
    echo "FAIL: wrapper still running after SIGTERM"; kill -9 "${WRAPPER}"; exit 1
fi
grep -q RCON_QUIT_CALLED "${WORK}/calls.log" 2>/dev/null \
    || { echo "FAIL: SIGTERM did not trigger an RCON quit"; cat "${WORK}/out.log"; exit 1; }
[ "${ELAPSED}" -le 8 ] \
    || { echo "FAIL: shutdown took ${ELAPSED}s; it should not wait out the grace period"; exit 1; }

echo "PASS: SIGTERM -> RCON quit -> exit in ${ELAPSED}s"
