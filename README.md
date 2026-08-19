# Project Zomboid Server

A self-hosted, containerised Project Zomboid dedicated server with automatic
timestamped world backups.

[![CI](https://github.com/fahadhewad/projectzomboid-server/actions/workflows/ci.yml/badge.svg)](https://github.com/fahadhewad/projectzomboid-server/actions/workflows/ci.yml)

```
docker compose up -d
```

That is the whole setup. One command brings up the game server and the backup
job; `docker compose down` tears it back down without touching your world.

---

## Why this exists

Running a modded PZ server for a few friends normally means a long, undocumented
sequence: install SteamCMD, install the server, wrestle a `.ini` file, remember
which of forty steps mattered when you rebuild the box a year later. And when a
mod update corrupts a save, you find out that "I'll set up backups later" was a
choice you already made.

This repo turns that into two files you can read and one command you can run.
The setup is code, so it is reproducible. The world is on a volume, so the
container is disposable. The backups happen whether or not anyone remembers.

## What it does

- **Reproducible server** — SteamCMD install, config generation and startup are
  all scripted. Rebuilding is one command, not an afternoon.
- **Config from `.env`** — `servertest.ini` is generated from environment
  variables at boot. No secrets in git, no hand-editing files inside a container.
- **Timestamped backups** — the world is archived on a timer as
  `pzsave_20260819T230000Z.tar.gz`, kept to a fixed number of copies.
- **Optional off-box copies** — one flag pushes those archives to any of ~70
  cloud backends via rclone. Off by default.
- **In-game announcements** — the server posts "World backed up" to chat over
  RCON, so players can see it working.
- **Zero runtime dependencies** — the tooling is pure Python standard library.

## Architecture

```
                    ┌─────────────────────────────────────────┐
   players ────────▶│  pz-server            16261/udp         │
   (UDP 16261/2)    │  ├── SteamCMD: install / update game    │
                    │  ├── render servertest.ini from .env    │
                    │  └── exec start-server.sh  (PID 1)      │
                    └──────────────────┬──────────────────────┘
                                       │
                             ┌─────────▼──────────┐
                             │  volume: pz-data   │
                             │  Saves/ Server/    │
                             │  mods/  Logs/      │
                             └─────────┬──────────┘
                                       │ read-only
                    ┌──────────────────▼──────────────────────┐
                    │  pz-backup                              │
                    │  ├── every N minutes: tar.gz the world  │
                    │  ├── keep newest N, delete the rest     │
                    │  ├── optional: rclone push + prune      │
                    │  └── announce in chat via RCON ─────────┼──▶ pz-server:27015
                    └──────────────────┬──────────────────────┘
                                       │
                             ┌─────────▼──────────┐        ┌──────────────┐
                             │ volume: pz-backups │ ─ ─ ─ ▶│  your cloud  │
                             │ pzsave_*.tar.gz    │ rclone │  (optional)  │
                             └────────────────────┘        └──────────────┘
```

Two containers rather than one, because they fail independently: a crashed
backup job must never kick players off, and a game restart must never interrupt
an upload.

## Quick start

**Requirements:** Docker with Compose v2, and two forwarded UDP ports.

```bash
git clone https://github.com/fahadhewad/projectzomboid-server.git
cd projectzomboid-server

cp .env.example .env
$EDITOR .env          # set PZ_ADMIN_PASSWORD and PZ_RCON_PASSWORD at minimum

docker compose up -d
docker compose logs -f
```

First boot downloads roughly 3 GB from Steam, so give it a few minutes. When the
log reads `SERVER STARTED`, friends can connect to your public IP on port 16261.

Compose refuses to start if `PZ_ADMIN_PASSWORD` or `PZ_RCON_PASSWORD` are unset,
rather than booting an unprotected server.

### Everyday commands

```bash
make up            # start           make logs        # follow logs
make down          # stop            make ps          # container status
make restart       # restart game    make backup-now  # backup immediately
make rcon CMD="players"              # run an admin command
```

`make help` lists everything.

### Restoring a world

```bash
docker compose stop pz-server
docker run --rm -v pz-data:/data -v pz-backups:/backups alpine \
    sh -c "rm -rf /data/Saves && tar -xzf /backups/pzsave_20260819T230000Z.tar.gz -C /data"
docker compose start pz-server
```

Stop the server first. Restoring underneath a running server gives you a world
half from the archive and half from the server's memory.

### Turning on off-box backups

Local archives live on the same disk as the server. That covers the likely
failure — a bad mod update, or someone doing something regrettable — but not a
dead drive. To copy them elsewhere:

```bash
rclone config                      # once; writes ./rclone/rclone.conf
```
```diff
# .env
-BACKUP_CLOUD_ENABLED=false
+BACKUP_CLOUD_ENABLED=true
+BACKUP_CLOUD_REMOTE=gdrive:pz-backups
```
```bash
docker compose up -d pz-backup
```

Any rclone backend works — S3, Backblaze B2, Google Drive, Dropbox, SFTP, a NAS.

> **Why not GitHub?** Git stores complete copies of binary files forever and
> never reclaims the space. A PZ world is binary chunk files, so hourly commits
> would blow past GitHub's 100 MB file limit and 1 GB repo guidance within days,
> with no way to shrink it afterwards. Git is the wrong tool for this one job.

## Configuration

Everything lives in `.env` — see `.env.example`, which documents every value.
The most common:

| Variable | Default | What it does |
|---|---|---|
| `PZ_SERVER_NAME` | `servertest` | Names the save folder. Changing it starts a **new world**. |
| `PZ_ADMIN_PASSWORD` | *required* | Admin login. Without it the server blocks on a prompt at boot. |
| `PZ_RCON_PASSWORD` | *required* | Admin commands and backup announcements. |
| `PZ_MAX_PLAYERS` | `8` | Player slots. |
| `PZ_MODS` / `PZ_WORKSHOP_ITEMS` | empty | Mod IDs and Workshop IDs. Order matters. Generate them with `pzops workshop`. |
| `PZ_STEAM_BRANCH` | empty | Steam branch for the server build. Set when a mod set needs a beta build. |
| `BACKUP_INTERVAL_MINUTES` | `60` | Backup frequency. |
| `BACKUP_KEEP_LOCAL` | `24` | Archives kept. 24 hourly = one rolling day. |
| `PZ_MEMORY_LIMIT` | `8g` | JVM ceiling. Raise it for heavy mod lists. |

The `pzops` tooling has further settings, overridable as
`PZOPS__SECTION__KEY` environment variables — see `src/pzops/config.py` for the
full set, or `config/pzops.example.toml` for the file form.

## Installing a Workshop collection

A collection ID is not a mod ID, and the server needs two different lists:
`WorkshopItems=` (numeric, what Steam downloads) and `Mods=` (internal IDs, what
the game loads). `pzops workshop` produces both.

```bash
# 1. Resolve the collection into Workshop IDs, dropping anything delisted
pzops workshop --collection 3773856464

# 2. Put that line in .env, set PZ_CONFIG_OVERWRITE=1, and boot once so the
#    server downloads every mod.

# 3. Read the real mod IDs out of what was downloaded
docker compose exec pz-server python3 -m pzops workshop \
    --collection 3773856464 \
    --workshop-dir /opt/pzserver/steamapps/workshop/content/108600
```

Step 3 reads each mod's own `mod.info` rather than scraping Workshop
descriptions. That matters: on a real 287-mod collection, description-scraping
left 6 mods with no stated ID and 33 declaring several with no way to tell
required from optional. The downloaded files are the only authoritative source.

## The `pzops` CLI

```bash
pzops render-config --templates /templates --dest /data/Server
pzops backup [--daemon]
pzops workshop --collection <id> [--workshop-dir DIR]
pzops rcon players
```

| Module | Responsibility |
|---|---|
| `config.py` | Layered config: defaults → TOML → environment |
| `template.py` | `${VAR}` rendering for the server `.ini` |
| `backup.py` | Archive creation, naming, rotation |
| `cloud.py` | rclone upload and remote pruning |
| `workshop.py` | Collection resolution and mod.info parsing |
| `rcon.py` | Source RCON client |
| `cli.py` | Argument parsing and wiring |

## Development

```bash
pip install -e ".[dev]"
make test     # pytest
make lint     # ruff check + format check
```

CI runs lint, the test suite on Python 3.11 and 3.12, shellcheck on the
entrypoint, and a Docker build of the sidecar image on every push.

## Design notes

A few decisions that are load-bearing, recorded so future-me remembers why.

**The game is not baked into the image.** SteamCMD downloads it into the volume
at boot. Baking in ~3 GB would mean rebuilding the image for every patch, and
redistributing game files the licence does not cover.

**The entrypoint ends in `exec`.** That makes the JVM PID 1, so `docker stop`
sends SIGTERM straight to the game and it saves the world before exiting.
Without `exec`, the signal stops the shell and the game is killed mid-write.
Combined with `stop_grace_period: 90s`, this is the difference between a clean
shutdown and a corrupted save.

**The backup container mounts the world read-only.** A backup job has no reason
to be able to write to the thing it is protecting, and `:ro` removes a whole
class of bug for one word.

**Archive names are UTC basic-ISO.** `pzsave_20260819T230000Z.tar.gz` sorts
chronologically and alphabetically at the same time, which is what lets both
local rotation and remote pruning be a sorted-list slice instead of date
parsing.

**Archives are written to `.part` and renamed.** Rename is atomic, so a crash
mid-backup leaves no truncated file that looks restorable.

**Secrets are referenced by variable name, not value.** Config files hold
`password_env = "RCON_PASSWORD"`. Config files get committed by accident;
environment variables do not.

**A failed upload is logged, not fatal.** A cloud outage must not cost you the
local archive or stop the schedule.

## Licence

MIT
