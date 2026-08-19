"""Cloud upload via rclone.

rclone is the pragmatic choice here: one binary, one config file, and it speaks
S3, B2, Google Drive, Dropbox and ~70 other backends. That means the backup code
stays provider-agnostic and the friend running the server picks whatever cloud
they already pay for.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class CloudError(RuntimeError):
    pass


class RcloneTarget:
    def __init__(
        self,
        remote: str,
        binary: str = "rclone",
        extra_args: list[str] | None = None,
        timeout: float = 1800.0,
    ) -> None:
        self.remote = remote.rstrip("/")
        self.binary = binary
        self.extra_args = list(extra_args or [])
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.remote) and shutil.which(self.binary) is not None

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [self.binary, *args, *self.extra_args]
        log.debug("rclone: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        if result.returncode != 0:
            raise CloudError(
                f"{' '.join(cmd)} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result

    def upload(self, path: Path) -> str:
        """Copy one file to the remote. Returns the remote path."""
        self._run(["copyto", str(path), f"{self.remote}/{path.name}"])
        return f"{self.remote}/{path.name}"

    def list_files(self, prefix: str = "") -> list[str]:
        result = self._run(["lsf", self.remote])
        names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return sorted(n for n in names if not prefix or n.startswith(prefix))

    def delete(self, name: str) -> None:
        self._run(["deletefile", f"{self.remote}/{name}"])

    def prune(self, keep: int, prefix: str = "") -> list[str]:
        """Keep the newest ``keep`` files by name (names are timestamp-sorted)."""
        if keep <= 0:
            return []
        names = self.list_files(prefix)
        doomed = names[: max(0, len(names) - keep)]
        for name in doomed:
            self.delete(name)
        return doomed
