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


def _fake_collection(monkeypatch, ids):
    from pzops import workshop

    monkeypatch.setattr(workshop, "fetch_collection", lambda c, **k: list(ids))
    monkeypatch.setattr(
        workshop,
        "fetch_details",
        lambda i, **k: [{"publishedfileid": x, "result": 1, "consumer_app_id": 108600} for x in i],
    )


def test_workshop_exclude_drops_the_item_and_its_mods(tmp_path, monkeypatch, capsys):
    """A version-mismatched mod must leave both lists, not just WorkshopItems."""
    _fake_collection(monkeypatch, ["100", "200"])
    content = tmp_path / "content"
    for wid, mod in (("100", "KeepMe"), ("200", "DropMe")):
        d = content / wid / "mods" / mod
        d.mkdir(parents=True)
        (d / "mod.info").write_text(f"name={mod}\nid={mod}\n")

    assert (
        cli.main(
            ["workshop", "--collection", "1", "--workshop-dir", str(content), "--exclude", "200"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "PZ_WORKSHOP_ITEMS=100\n" in out
    assert "PZ_MODS=KeepMe\n" in out
    assert "DropMe" not in out and "200" not in out


def test_workshop_exclude_mod_keeps_the_download_but_drops_the_mod(tmp_path, monkeypatch, capsys):
    """One item can ship alternatives that collide; drop one without losing the item."""
    _fake_collection(monkeypatch, ["100"])
    base = content = tmp_path / "content"
    for mod in ("DarkWpnSlings", "InvisibleWpnSlings"):
        d = base / "100" / "mods" / mod
        d.mkdir(parents=True)
        (d / "mod.info").write_text(f"name={mod}\nid={mod}\n")

    assert (
        cli.main(
            [
                "workshop",
                "--collection",
                "1",
                "--workshop-dir",
                str(content),
                "--exclude-mod",
                "InvisibleWpnSlings",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    # The Workshop item stays, so the surviving variant still has its files.
    assert "PZ_WORKSHOP_ITEMS=100\n" in out
    assert "PZ_MODS=DarkWpnSlings\n" in out
    assert "InvisibleWpnSlings" not in out


def test_workshop_warns_about_an_unknown_mod_id(tmp_path, monkeypatch, caplog):
    _fake_collection(monkeypatch, ["100"])
    d = tmp_path / "content" / "100" / "mods" / "RealMod"
    d.mkdir(parents=True)
    (d / "mod.info").write_text("name=RealMod\nid=RealMod\n")

    cli.main(
        [
            "workshop",
            "--collection",
            "1",
            "--workshop-dir",
            str(tmp_path / "content"),
            "--exclude-mod",
            "TypoMod",
        ]
    )
    assert "no such mod ID" in caplog.text
