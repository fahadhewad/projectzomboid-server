import pytest

from pzops import workshop


def fake_post(collection_children=None, details=None):
    def _post(endpoint, fields, timeout=30.0):
        if endpoint == "GetCollectionDetails":
            if collection_children is None:
                return {"response": {"collectiondetails": [{"result": 9}]}}
            return {
                "response": {
                    "collectiondetails": [
                        {"result": 1, "publishedfileid": "1", "children": collection_children}
                    ]
                }
            }
        return {"response": {"publishedfiledetails": details or []}}

    return _post


def test_collection_preserves_author_load_order():
    children = [
        {"publishedfileid": "300", "sortorder": 2},
        {"publishedfileid": "100", "sortorder": 0},
        {"publishedfileid": "200", "sortorder": 1},
    ]
    # PZ load order is significant, so sortorder wins over any natural sort.
    assert workshop.fetch_collection("1", post=fake_post(children)) == ["100", "200", "300"]


def test_unresolvable_collection_raises_with_guidance():
    with pytest.raises(workshop.WorkshopError, match="collection"):
        workshop.fetch_collection("1", post=fake_post(None))


def test_unresolved_items_are_detected():
    details = [
        {"publishedfileid": "100", "result": 1, "consumer_app_id": 108600},
        {"publishedfileid": "200", "result": 9},
    ]
    assert workshop.unresolved(details) == ["200"]


def test_items_from_other_games_are_detected():
    details = [
        {"publishedfileid": "100", "result": 1, "consumer_app_id": 108600},
        {"publishedfileid": "200", "result": 1, "consumer_app_id": 294100},
    ]
    assert workshop.wrong_app(details) == ["200"]


def write_mod(root, workshop_id, folder, mod_id, name="A Mod", build=None):
    path = root / workshop_id / "mods" / folder
    if build:
        path = path / build
    path.mkdir(parents=True)
    (path / "mod.info").write_text(f"name={name}\nid={mod_id}\ndescription=x\n")


@pytest.fixture
def content(tmp_path):
    return tmp_path / "content"


def test_scan_reads_mod_ids_from_disk(content):
    write_mod(content, "100", "BetterElectronics", "LWBetterElectronics")
    scanned = workshop.scan_workshop(content)
    assert [m.mod_id for m in scanned["100"]] == ["LWBetterElectronics"]


def test_scan_finds_build42_nested_layout(content):
    """Build 42 nests mod.info under a build folder; Build 41 does not."""
    write_mod(content, "100", "SomeMod", "SomeModId", build="42")
    assert [m.mod_id for m in workshop.scan_workshop(content)["100"]] == ["SomeModId"]


def test_one_workshop_item_can_ship_several_mods(content):
    # e.g. a car pack with optional variants - the case description-scraping
    # gets wrong, because the description lists them all with no structure.
    write_mod(content, "100", "Mini", "69mini")
    write_mod(content, "100", "MiniBean", "69mini_MrBean")
    assert sorted(m.mod_id for m in workshop.scan_workshop(content)["100"]) == [
        "69mini",
        "69mini_MrBean",
    ]


def test_mod_without_an_id_is_skipped(content):
    path = content / "100" / "mods" / "Broken"
    path.mkdir(parents=True)
    (path / "mod.info").write_text("name=No ID Here\n")
    assert workshop.scan_workshop(content) == {}


def test_mod_info_parsing_tolerates_bom_comments_and_spacing(tmp_path):
    path = tmp_path / "mod.info"
    path.write_text("﻿# comment\n\n  name = Spaced Mod  \nID = MyModId \n", encoding="utf-8")
    info = workshop.parse_mod_info(path)
    assert info.mod_id == "MyModId"
    assert info.name == "Spaced Mod"


def test_missing_workshop_dir_raises():
    with pytest.raises(workshop.WorkshopError, match="not found"):
        workshop.scan_workshop("/definitely/not/here")


def test_ordering_follows_the_collection(content):
    write_mod(content, "100", "First", "FirstMod")
    write_mod(content, "200", "Second", "SecondMod")
    scanned = workshop.scan_workshop(content)
    ordered = workshop.order_mods(["200", "100"], scanned)
    assert [m.mod_id for m in ordered] == ["SecondMod", "FirstMod"]


def test_extra_downloaded_mods_are_appended_not_dropped(content):
    write_mod(content, "100", "InCollection", "InCollection")
    write_mod(content, "999", "Manual", "ManuallyAdded")
    ordered = workshop.order_mods(["100"], workshop.scan_workshop(content))
    assert [m.mod_id for m in ordered] == ["InCollection", "ManuallyAdded"]


def test_missing_download_is_skipped_rather_than_failing(content):
    write_mod(content, "100", "Present", "PresentMod")
    ordered = workshop.order_mods(["100", "404"], workshop.scan_workshop(content))
    assert [m.mod_id for m in ordered] == ["PresentMod"]


def test_format_lines_uses_semicolons(content):
    write_mod(content, "100", "A", "ModA")
    write_mod(content, "200", "B", "ModB")
    ordered = workshop.order_mods(["100", "200"], workshop.scan_workshop(content))
    items, mods = workshop.format_lines(["100", "200"], ordered)
    assert items == "PZ_WORKSHOP_ITEMS=100;200"
    assert mods == "PZ_MODS=ModA;ModB"
