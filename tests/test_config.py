from pzops import config


def test_defaults_are_available_by_dotted_path():
    cfg = config.load(environ={})
    assert cfg.get("backup.interval_minutes") == 60
    assert cfg.get("backup.cloud.enabled") is False
    assert cfg.get("nope.missing", "fallback") == "fallback"


def test_env_override_coerces_to_the_default_type():
    cfg = config.load(
        environ={
            "PZOPS__BACKUP__INTERVAL_MINUTES": "15",
            "PZOPS__BACKUP__CLOUD__ENABLED": "true",
            "PZOPS__RCON__TIMEOUT_SECONDS": "2.5",
        }
    )
    # Environment variables are strings; a string 15 would break arithmetic.
    assert cfg.get("backup.interval_minutes") == 15
    assert isinstance(cfg.get("backup.interval_minutes"), int)
    assert cfg.get("backup.cloud.enabled") is True
    assert cfg.get("rcon.timeout_seconds") == 2.5


def test_env_override_splits_lists_on_commas():
    cfg = config.load(environ={"PZOPS__BACKUP__CLOUD__EXTRA_ARGS": "--fast-list, --transfers=4"})
    assert cfg.get("backup.cloud.extra_args") == ["--fast-list", "--transfers=4"]


def test_unrelated_env_vars_are_ignored():
    cfg = config.load(environ={"PATH": "/usr/bin", "BACKUP__KEEP_LOCAL": "1"})
    assert cfg.get("backup.keep_local") == 24


def test_secrets_are_read_by_indirection_not_stored():
    cfg = config.load(environ={"RCON_PASSWORD": "hunter2"})
    # The config holds the variable *name*; the value comes from the environment.
    assert cfg.get("rcon.password_env") == "RCON_PASSWORD"
    assert cfg.secret("rcon.password_env") == "hunter2"


def test_missing_secret_returns_empty_rather_than_raising():
    cfg = config.load(environ={})
    assert cfg.secret("rcon.password_env") == ""


def test_toml_file_layers_between_defaults_and_env(tmp_path):
    path = tmp_path / "pzops.toml"
    path.write_text('[backup]\nkeep_local = 5\nname_prefix = "fromfile"\n')

    cfg = config.load(path, environ={"PZOPS__BACKUP__KEEP_LOCAL": "9"})
    assert cfg.get("backup.keep_local") == 9  # env beats file
    assert cfg.get("backup.name_prefix") == "fromfile"  # file beats default
    assert cfg.get("backup.interval_minutes") == 60  # default survives


def test_missing_explicit_config_file_is_an_error(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        config.load(tmp_path / "absent.toml", environ={})


def test_deep_merge_does_not_mutate_its_inputs():
    base = {"a": {"x": 1, "y": 2}}
    config.deep_merge(base, {"a": {"y": 99}})
    assert base == {"a": {"x": 1, "y": 2}}
