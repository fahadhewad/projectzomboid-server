import tarfile
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pzops import backup


@pytest.fixture
def world(tmp_path):
    """A stand-in for /data/Saves."""
    saves = tmp_path / "Saves" / "Multiplayer" / "servertest"
    saves.mkdir(parents=True)
    (saves / "map_p.bin").write_bytes(b"chunk" * 1000)
    (saves / "players.db").write_bytes(b"players")
    return tmp_path / "Saves"


def at(hour):
    return datetime(2026, 8, 19, hour, 0, tzinfo=UTC)


def test_archive_name_is_utc_and_sortable():
    assert backup.archive_name("pzsave", at(21)) == "pzsave_20260819T210000Z.tar.gz"


def test_names_sort_chronologically_as_plain_strings():
    # Rotation and remote pruning both rely on this, so it is worth pinning.
    names = [
        backup.archive_name("pzsave", at(9)),
        backup.archive_name("pzsave", at(10)),
        backup.archive_name("pzsave", at(21)),
    ]
    assert names == sorted(names)


def test_timestamp_converts_non_utc_input():
    berlin = datetime(2026, 8, 19, 23, 0, tzinfo=timezone(timedelta(hours=2)))
    assert backup.timestamp(berlin) == "20260819T210000Z"


def test_archive_contains_the_world(world, tmp_path):
    dest = backup.create_archive(world, tmp_path / "out" / "x.tar.gz")
    with tarfile.open(dest) as tar:
        names = tar.getnames()
    assert any(n.endswith("map_p.bin") for n in names)
    assert any(n.endswith("players.db") for n in names)


def test_archive_is_written_atomically(world, tmp_path):
    staging = tmp_path / "backups"
    backup.create_archive(world, staging / "x.tar.gz")
    # A .part left behind would eventually be restored as if it were complete.
    assert list(staging.glob("*.part")) == []


def test_missing_source_raises_rather_than_writing_an_empty_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.create_archive(tmp_path / "absent", tmp_path / "x.tar.gz")


def test_rotation_keeps_the_newest(world, tmp_path):
    staging = tmp_path / "backups"
    for hour in range(5):
        backup.run_backup(world, staging, keep_local=3, now=at(hour))

    kept = sorted(p.name for p in staging.glob("*.tar.gz"))
    assert kept == [
        "pzsave_20260819T020000Z.tar.gz",
        "pzsave_20260819T030000Z.tar.gz",
        "pzsave_20260819T040000Z.tar.gz",
    ]


def test_keep_local_zero_disables_rotation(world, tmp_path):
    staging = tmp_path / "backups"
    for hour in range(3):
        backup.run_backup(world, staging, keep_local=0, now=at(hour))
    assert len(list(staging.glob("*.tar.gz"))) == 3


def test_rotation_ignores_other_prefixes(tmp_path):
    staging = tmp_path / "backups"
    staging.mkdir()
    for name in ["pzsave_1.tar.gz", "pzsave_2.tar.gz", "other_1.tar.gz"]:
        (staging / name).write_bytes(b"x")

    backup.prune_local(staging, "pzsave", keep=1)
    remaining = sorted(p.name for p in staging.glob("*.tar.gz"))
    assert remaining == ["other_1.tar.gz", "pzsave_2.tar.gz"]


class FakeCloud:
    def __init__(self, available=True, fail=False):
        self.available = available
        self.fail = fail
        self.uploaded = []
        self.pruned_with = None

    def upload(self, path):
        if self.fail:
            from pzops.cloud import CloudError

            raise CloudError("network down")
        self.uploaded.append(path.name)
        return f"remote:/{path.name}"

    def prune(self, keep, prefix=""):
        self.pruned_with = (keep, prefix)
        return ["pzsave_old.tar.gz"]


def test_backup_uploads_and_prunes_remotely(world, tmp_path):
    cloud = FakeCloud()
    result = backup.run_backup(world, tmp_path / "b", cloud=cloud, keep_remote=10, now=at(21))

    assert cloud.uploaded == ["pzsave_20260819T210000Z.tar.gz"]
    assert result.remote_path == "remote:/pzsave_20260819T210000Z.tar.gz"
    assert cloud.pruned_with == (10, "pzsave_")
    assert result.pruned_remote == ["pzsave_old.tar.gz"]


def test_upload_failure_keeps_the_local_archive(world, tmp_path):
    """A cloud outage must not cost us the backup we just made."""
    staging = tmp_path / "b"
    result = backup.run_backup(world, staging, cloud=FakeCloud(fail=True), now=at(21))

    assert result.remote_path is None
    assert result.path.exists()
    assert result.size_bytes > 0


def test_unavailable_cloud_is_skipped_not_fatal(world, tmp_path):
    result = backup.run_backup(world, tmp_path / "b", cloud=FakeCloud(available=False), now=at(21))
    assert result.remote_path is None
    assert result.path.exists()
