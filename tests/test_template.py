import pytest

from pzops import template


def test_substitutes_known_variables():
    assert template.render("Public=${PZ_PUBLIC}", {"PZ_PUBLIC": "false"}) == "Public=false"


def test_uses_default_when_variable_is_absent():
    assert template.render("MaxPlayers=${N:-8}", {}) == "MaxPlayers=8"


def test_empty_default_renders_empty():
    assert template.render("Password=${P:-}", {}) == "Password="


def test_empty_value_falls_back_to_default():
    # compose passes unset variables through as "", which must not defeat a default.
    assert template.render("MaxPlayers=${N:-8}", {"N": ""}) == "MaxPlayers=8"


def test_missing_variable_without_default_raises():
    with pytest.raises(template.MissingVariableError) as excinfo:
        template.render("RCONPassword=${PZ_RCON_PASSWORD}", {})
    # The error must name the variable, or debugging a boot failure is guesswork.
    assert "PZ_RCON_PASSWORD" in str(excinfo.value)


def test_all_missing_variables_are_reported_at_once():
    with pytest.raises(template.MissingVariableError) as excinfo:
        template.render("${A}\n${B}", {})
    message = str(excinfo.value)
    assert "A" in message and "B" in message


def test_render_file_writes_and_reports_change(tmp_path):
    src = tmp_path / "s.ini.tmpl"
    src.write_text("Name=${N}")
    dest = tmp_path / "s.ini"

    assert template.render_file(src, dest, {"N": "zomboid"}) is True
    assert dest.read_text() == "Name=zomboid"
    # Re-rendering identical content is not a change, so nothing is rewritten.
    assert template.render_file(src, dest, {"N": "zomboid"}) is False


def test_no_overwrite_preserves_hand_edits(tmp_path):
    src = tmp_path / "s.ini.tmpl"
    src.write_text("Name=${N}")
    dest = tmp_path / "s.ini"
    dest.write_text("Name=hand-edited")

    assert template.render_file(src, dest, {"N": "zomboid"}, overwrite=False) is False
    assert dest.read_text() == "Name=hand-edited"


def test_render_tree_drops_the_suffix(tmp_path):
    src_dir, dest_dir = tmp_path / "t", tmp_path / "out"
    src_dir.mkdir()
    (src_dir / "servertest.ini.tmpl").write_text("Port=${P:-16261}")
    (src_dir / "notes.txt").write_text("ignored")

    written = template.render_tree(src_dir, dest_dir, {})
    assert [p.name for p in written] == ["servertest.ini"]
    assert (dest_dir / "servertest.ini").read_text() == "Port=16261"
