"""Timestamped save backups with local rotation and a cloud push.

Naming is ``<prefix>_<UTC ISO basic>.tar.gz`` — e.g. ``pzsave_20260819T211403Z.tar.gz``.
That format sorts lexicographically in the same order it sorts chronologically,
which is what lets rotation and remote pruning be a plain sorted-list slice.
"""

from __future__ import annotations

import logging
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .cloud import CloudError, RcloneTarget

log = logging.getLogger(__name__)

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def timestamp(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def archive_name(prefix: str, now: datetime | None = None) -> str:
    return f"{prefix}_{timestamp(now)}.tar.gz"


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    duration_seconds: float
    remote_path: str | None = None
    pruned_local: list[str] = None  # type: ignore[assignment]
    pruned_remote: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.pruned_local = self.pruned_local or []
        self.pruned_remote = self.pruned_remote or []


def create_archive(source_dir: Path, dest: Path) -> Path:
    """Tar+gzip ``source_dir`` into ``dest``, written atomically via a ``.part`` file."""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"backup source not found: {source_dir}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(source_dir, arcname=source_dir.name)
        partial.replace(dest)
    finally:
        partial.unlink(missing_ok=True)
    return dest


def prune_local(directory: Path, prefix: str, keep: int) -> list[str]:
    if keep <= 0:
        return []
    archives = sorted(p for p in directory.glob(f"{prefix}_*.tar.gz") if p.is_file())
    doomed = archives[: max(0, len(archives) - keep)]
    for path in doomed:
        path.unlink(missing_ok=True)
    return [p.name for p in doomed]


def run_backup(
    source_dir: Path,
    staging_dir: Path,
    prefix: str = "pzsave",
    keep_local: int = 24,
    cloud: RcloneTarget | None = None,
    keep_remote: int = 0,
    now: datetime | None = None,
) -> BackupResult:
    started = time.monotonic()
    staging_dir = Path(staging_dir)
    dest = staging_dir / archive_name(prefix, now)
    create_archive(Path(source_dir), dest)
    result = BackupResult(
        path=dest,
        size_bytes=dest.stat().st_size,
        duration_seconds=time.monotonic() - started,
    )

    if cloud is not None and cloud.available:
        try:
            result.remote_path = cloud.upload(dest)
            if keep_remote > 0:
                result.pruned_remote = cloud.prune(keep_remote, prefix=f"{prefix}_")
        except CloudError:
            # A cloud outage must not cost us the local archive or stop the schedule.
            log.exception("cloud upload failed for %s; keeping local copy", dest.name)
    elif cloud is not None:
        log.warning("cloud target configured but unavailable (rclone missing or no remote)")

    result.pruned_local = prune_local(staging_dir, prefix, keep_local)
    return result
