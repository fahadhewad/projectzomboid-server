from pzops import cli


def test_help_lists_every_command(capsys):
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for command in ("render-config", "backup", "rcon"):
        assert command in out


def test_render_config_writes_the_server_ini(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "servertest.ini.tmpl").write_text(
        "RCONPassword=${PZ_RCON_PASSWORD}\nMaxPlayers=${PZ_MAX_PLAYERS:-8}\n"
    )
    dest = tmp_path / "Server"
    monkeypatch.setenv("PZ_RCON_PASSWORD", "s3cret")

    assert cli.main(["render-config", "--templates", str(templates), "--dest", str(dest)]) == 0
    assert (dest / "servertest.ini").read_text() == "RCONPassword=s3cret\nMaxPlayers=8\n"


def test_backup_runs_once_and_reports_success(tmp_path, monkeypatch):
    saves = tmp_path / "Saves"
    saves.mkdir()
    (saves / "map_p.bin").write_bytes(b"world")
    monkeypatch.setenv("PZOPS__BACKUP__SOURCE_DIR", str(saves))
    monkeypatch.setenv("PZOPS__BACKUP__STAGING_DIR", str(tmp_path / "backups"))

    assert cli.main(["backup"]) == 0
    assert len(list((tmp_path / "backups").glob("pzsave_*.tar.gz"))) == 1


def test_backup_reports_failure_when_the_world_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PZOPS__BACKUP__SOURCE_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("PZOPS__BACKUP__STAGING_DIR", str(tmp_path / "backups"))
    # A non-zero exit is what makes a failed backup visible to cron/healthchecks.
    assert cli.main(["backup"]) == 1


def test_rcon_without_a_password_exits_with_a_clear_code(monkeypatch):
    monkeypatch.delenv("RCON_PASSWORD", raising=False)
    assert cli.main(["rcon", "players"]) == 2


def test_rcon_reports_an_unreachable_server_without_a_traceback(monkeypatch, caplog):
    """A server still loading mods is the common case; it must not look like a crash."""
    monkeypatch.setenv("RCON_PASSWORD", "secret")
    # Port 1 is reserved and refuses immediately.
    monkeypatch.setenv("PZOPS__RCON__PORT", "1")
    monkeypatch.setenv("PZOPS__RCON__TIMEOUT_SECONDS", "1")

    assert cli.main(["rcon", "players"]) == 3
    assert "still" in caplog.text and "docker compose logs" in caplog.text
