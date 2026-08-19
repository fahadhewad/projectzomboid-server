"""Command line entry point: ``pzops <command>``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from . import config as config_module
from . import workshop as workshop_module
from .backup import run_backup
from .cloud import RcloneTarget
from .rcon import RconClient, try_command
from .template import render_tree

log = logging.getLogger("pzops")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _announcer(cfg: config_module.Config):
    """Return a callable that posts a server message over RCON, or None."""
    password = cfg.secret("rcon.password_env")
    if not password:
        return None

    def announce(text: str) -> None:
        try_command(
            cfg.get("rcon.host"),
            int(cfg.get("rcon.port")),
            password,
            f'servermsg "{text}"',
            float(cfg.get("rcon.timeout_seconds", 5.0)),
        )

    return announce


def cmd_render_config(args: argparse.Namespace, cfg: config_module.Config) -> int:
    written = render_tree(
        Path(args.templates), Path(args.dest), dict(os.environ), overwrite=not args.no_overwrite
    )
    for path in written:
        log.info("rendered %s", path)
    if not written:
        log.info("no templates needed rendering")
    return 0


def cmd_backup(args: argparse.Namespace, cfg: config_module.Config) -> int:
    cloud_cfg = cfg.section("backup.cloud")
    cloud = None
    if cloud_cfg.get("enabled"):
        cloud = RcloneTarget(
            remote=str(cloud_cfg.get("rclone_remote", "")),
            binary=str(cloud_cfg.get("rclone_binary", "rclone")),
            extra_args=list(cloud_cfg.get("extra_args", []) or []),
        )
        if not cloud.available:
            log.warning("cloud backups enabled but rclone or remote is not configured")

    announce = _announcer(cfg) if cfg.get("backup.announce") else None
    interval = max(1, int(cfg.get("backup.interval_minutes", 60))) * 60
    stop = threading.Event()

    def once() -> int:
        try:
            result = run_backup(
                source_dir=Path(cfg.get("backup.source_dir")),
                staging_dir=Path(cfg.get("backup.staging_dir")),
                prefix=str(cfg.get("backup.name_prefix", "pzsave")),
                keep_local=int(cfg.get("backup.keep_local", 24)),
                cloud=cloud,
                keep_remote=int(cloud_cfg.get("keep_remote", 0) or 0),
            )
        except FileNotFoundError as exc:
            # Normal on first boot: the server has not generated a world yet.
            # A stack trace here reads like a crash, so say what is happening.
            log.warning("no backup taken - %s (has the server created a world yet?)", exc)
            return 1
        log.info(
            "backup %s (%.1f MiB in %.1fs)%s%s",
            result.path.name,
            result.size_bytes / (1024 * 1024),
            result.duration_seconds,
            f" -> {result.remote_path}" if result.remote_path else " [local only]",
            f" pruned {len(result.pruned_local)} local/{len(result.pruned_remote)} remote"
            if result.pruned_local or result.pruned_remote
            else "",
        )
        if announce is not None:
            announce(f"World backed up: {result.path.name}")
        return 0

    if not args.daemon:
        return once()

    _install_signal_handlers(stop)
    log.info("backup daemon started; interval %d minutes", interval // 60)
    while not stop.is_set():
        once()
        stop.wait(interval)
    log.info("backup daemon stopped")
    return 0


def cmd_workshop(args: argparse.Namespace, cfg: config_module.Config) -> int:
    """Resolve a collection, and/or read mod IDs out of downloaded mods."""
    workshop_ids: list[str] = []

    if args.collection:
        workshop_ids = workshop_module.fetch_collection(args.collection)
        log.info("collection %s contains %d items", args.collection, len(workshop_ids))

        details = workshop_module.fetch_details(workshop_ids)
        dead = workshop_module.unresolved(details)
        foreign = workshop_module.wrong_app(details)
        if dead:
            # Left in the list, the server retries an impossible download forever.
            log.warning(
                "%d item(s) could not be resolved and were dropped: %s", len(dead), ", ".join(dead)
            )
            workshop_ids = [i for i in workshop_ids if i not in set(dead)]
        if foreign:
            log.warning(
                "%d item(s) are not Project Zomboid mods and were dropped: %s",
                len(foreign),
                ", ".join(foreign),
            )
            workshop_ids = [i for i in workshop_ids if i not in set(foreign)]

    mods: list[workshop_module.ModInfo] = []
    if args.workshop_dir:
        scanned = workshop_module.scan_workshop(args.workshop_dir)
        log.info("scanned %d downloaded Workshop item(s)", len(scanned))
        mods = workshop_module.order_mods(workshop_ids or sorted(scanned), scanned)
        log.info("found %d loadable mod(s)", len(mods))
        if workshop_ids:
            missing = [i for i in workshop_ids if i not in scanned]
            if missing:
                log.warning(
                    "%d collection item(s) are not downloaded yet: %s",
                    len(missing),
                    ", ".join(missing[:10]),
                )

    items_line, mods_line = workshop_module.format_lines(workshop_ids, mods)
    output = []
    if workshop_ids:
        output.append(items_line)
    if mods:
        output.append(mods_line)
    if not output:
        log.error("nothing to do: pass --collection and/or --workshop-dir")
        return 2

    text = "\n".join(output) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        log.info("wrote %s", args.output)
    else:
        print(text, end="")
    return 0


def cmd_rcon(args: argparse.Namespace, cfg: config_module.Config) -> int:
    password = cfg.secret("rcon.password_env")
    if not password:
        log.error("no RCON password set (%s is empty)", cfg.get("rcon.password_env"))
        return 2
    with RconClient(
        cfg.get("rcon.host"),
        int(cfg.get("rcon.port")),
        password,
        float(cfg.get("rcon.timeout_seconds", 5.0)),
    ) as client:
        print(client.command(" ".join(args.command)))
    return 0


def _install_signal_handlers(stop: threading.Event) -> None:
    """Stop the daemon loop on SIGTERM/SIGINT so `docker compose down` is clean."""

    def handler(signum: int, _frame: object) -> None:
        log.info("received signal %s, shutting down", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pzops", description=__doc__)
    parser.add_argument("--version", action="version", version=f"pzops {__version__}")
    parser.add_argument("-c", "--config", help="path to pzops.toml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render-config", help="render server config templates from env")
    render.add_argument("--templates", default="/templates")
    render.add_argument("--dest", default="/data/Server")
    render.add_argument(
        "--no-overwrite",
        action="store_true",
        help="keep hand-edits: only create files that do not exist yet",
    )
    render.set_defaults(func=cmd_render_config)

    backup = sub.add_parser("backup", help="create a timestamped save archive")
    backup.add_argument("--daemon", action="store_true", help="run on the configured interval")
    backup.set_defaults(func=cmd_backup)

    shop = sub.add_parser("workshop", help="turn a Workshop collection into .env lines")
    shop.add_argument(
        "--collection",
        metavar="ID",
        help="Workshop collection ID to resolve into Workshop item IDs",
    )
    shop.add_argument(
        "--workshop-dir",
        metavar="DIR",
        help="downloaded Workshop content dir, to read real mod IDs from "
        "mod.info (e.g. /opt/pzserver/steamapps/workshop/content/108600)",
    )
    shop.add_argument("-o", "--output", metavar="FILE", help="write to a file instead of stdout")
    shop.set_defaults(func=cmd_workshop)

    rcon = sub.add_parser("rcon", help="send one RCON command to the server")
    rcon.add_argument("command", nargs="+")
    rcon.set_defaults(func=cmd_rcon)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = config_module.load(args.config)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2
    try:
        return int(args.func(args, cfg) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
