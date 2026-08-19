import subprocess

import pytest

from pzops.cloud import CloudError, RcloneTarget


class FakeRun:
    """Records rclone invocations instead of running them."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "boom")


@pytest.fixture
def target(monkeypatch):
    def _make(stdout="", returncode=0):
        fake = FakeRun(stdout, returncode)
        monkeypatch.setattr(subprocess, "run", fake)
        rclone = RcloneTarget("gdrive:pz/saves")
        return rclone, fake

    return _make


def test_upload_uses_copyto_so_the_name_is_preserved(target, tmp_path):
    rclone, fake = target()
    archive = tmp_path / "pzsave_20260819T210000Z.tar.gz"
    archive.write_bytes(b"x")

    assert rclone.upload(archive) == "gdrive:pz/saves/pzsave_20260819T210000Z.tar.gz"
    assert fake.calls[0][:2] == ["rclone", "copyto"]


def test_trailing_slash_in_remote_does_not_double_up(monkeypatch):
    assert RcloneTarget("gdrive:pz/saves/").remote == "gdrive:pz/saves"


def test_nonzero_exit_raises_with_stderr(target, tmp_path):
    rclone, _ = target(returncode=1)
    archive = tmp_path / "a.tar.gz"
    archive.write_bytes(b"x")

    with pytest.raises(CloudError, match="boom"):
        rclone.upload(archive)


def test_prune_deletes_all_but_the_newest(target):
    listing = "pzsave_1.tar.gz\npzsave_2.tar.gz\npzsave_3.tar.gz\n"
    rclone, fake = target(stdout=listing)

    assert rclone.prune(keep=1, prefix="pzsave_") == ["pzsave_1.tar.gz", "pzsave_2.tar.gz"]
    deleted = [c[-1] for c in fake.calls if c[1] == "deletefile"]
    assert deleted == ["gdrive:pz/saves/pzsave_1.tar.gz", "gdrive:pz/saves/pzsave_2.tar.gz"]


def test_prune_keeps_everything_when_under_the_limit(target):
    rclone, fake = target(stdout="pzsave_1.tar.gz\n")
    assert rclone.prune(keep=5, prefix="pzsave_") == []
    assert not [c for c in fake.calls if c[1] == "deletefile"]


def test_prune_zero_is_a_no_op(target):
    """keep=0 means "unlimited", not "delete everything"."""
    rclone, fake = target(stdout="pzsave_1.tar.gz\n")
    assert rclone.prune(keep=0) == []
    assert fake.calls == []


def test_prune_ignores_files_with_another_prefix(target):
    rclone, _ = target(stdout="pzsave_1.tar.gz\nunrelated.tar.gz\npzsave_2.tar.gz\n")
    assert rclone.prune(keep=1, prefix="pzsave_") == ["pzsave_1.tar.gz"]


def test_target_is_unavailable_without_a_remote():
    assert RcloneTarget("").available is False
