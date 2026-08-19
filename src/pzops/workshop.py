"""Steam Workshop collection handling.

Installing a mod collection needs two different lists in the server config, and
they are not the same thing:

    WorkshopItems=  numeric Workshop IDs, which Steam uses to download
    Mods=           internal mod IDs, which the game uses to load

A collection's URL gives you neither directly. This module resolves a
collection into its Workshop IDs via Steam's public API, and reads the internal
mod IDs out of each downloaded mod's ``mod.info``.

Reading ``mod.info`` matters: the usual advice is to copy mod IDs out of the
Workshop description, but authors write those by hand. On a real 287-mod
collection that left 6 mods with no stated ID at all and 33 declaring several,
with no way to tell required from optional. The downloaded files are the only
authoritative source.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STEAM_API = "https://api.steampowered.com/ISteamRemoteStorage"
PZ_APP_ID = "108600"

# Steam reports result=1 for a resolved item; anything else means the item is
# private, deleted, or was never visible to us.
RESULT_OK = 1


class WorkshopError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModInfo:
    """One loadable mod, as declared by its own mod.info."""

    mod_id: str
    name: str
    workshop_id: str
    path: Path


def _post(endpoint: str, fields: dict[str, str], timeout: float = 30.0) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(f"{STEAM_API}/{endpoint}/v1/", data=data, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _numeric_fields(ids: list[str], count_key: str) -> dict[str, str]:
    fields = {count_key: str(len(ids))}
    for index, value in enumerate(ids):
        fields[f"publishedfileids[{index}]"] = value
    return fields


def fetch_collection(collection_id: str, post=_post) -> list[str]:
    """Resolve a collection to its member Workshop IDs, in the author's order.

    Collection order is the author's intended load order, and PZ load order is
    significant, so it is preserved rather than sorted.
    """
    payload = post("GetCollectionDetails", _numeric_fields([collection_id], "collectioncount"))
    details = payload.get("response", {}).get("collectiondetails") or []
    if not details or details[0].get("result") != RESULT_OK:
        raise WorkshopError(
            f"collection {collection_id} could not be resolved - check the ID is a "
            f"collection (not a single mod) and that it is public"
        )
    children = details[0].get("children") or []
    ordered = sorted(children, key=lambda child: child.get("sortorder", 0))
    return [child["publishedfileid"] for child in ordered]


def fetch_details(workshop_ids: list[str], post=_post) -> list[dict]:
    """Fetch metadata for Workshop items. Returns raw entries, unresolved included."""
    if not workshop_ids:
        return []
    payload = post("GetPublishedFileDetails", _numeric_fields(workshop_ids, "itemcount"))
    return payload.get("response", {}).get("publishedfiledetails") or []


def unresolved(details: list[dict]) -> list[str]:
    """Workshop IDs Steam could not resolve — delisted, private, or removed.

    These matter: leaving one in WorkshopItems means the server retries a
    download that can never succeed on every single boot.
    """
    return [d.get("publishedfileid", "?") for d in details if d.get("result") != RESULT_OK]


def wrong_app(details: list[dict], app_id: str = PZ_APP_ID) -> list[str]:
    """Items belonging to another game, which would never load."""
    return [
        d.get("publishedfileid", "?")
        for d in details
        if d.get("result") == RESULT_OK and str(d.get("consumer_app_id", app_id)) != app_id
    ]


def parse_mod_info(path: Path) -> ModInfo | None:
    """Read one mod.info. Returns None if it declares no usable id."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key not in values:  # first declaration wins
            values[key] = value.strip()
    mod_id = values.get("id", "")
    if not mod_id:
        return None
    # <workshop_dir>/<workshop_id>/mods/<ModFolder>[/<build>]/mod.info
    workshop_id = ""
    for parent in path.parents:
        if parent.parent is not None and parent.parent.name == "content":
            workshop_id = parent.name
            break
    return ModInfo(
        mod_id=mod_id, name=values.get("name", mod_id), workshop_id=workshop_id, path=path
    )


def scan_workshop(workshop_dir: str | Path) -> dict[str, list[ModInfo]]:
    """Map Workshop ID -> the mods it contains.

    One Workshop item can ship several loadable mods (a car pack with optional
    variants, say), which is exactly the case description-scraping gets wrong.
    The search is recursive because Build 42 nests mod.info under a build
    subfolder while Build 41 does not.
    """
    root = Path(workshop_dir)
    if not root.is_dir():
        raise WorkshopError(f"workshop directory not found: {root}")

    found: dict[str, list[ModInfo]] = {}
    for item_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        mods: list[ModInfo] = []
        seen: set[str] = set()
        for info_path in sorted(item_dir.rglob("mod.info")):
            info = parse_mod_info(info_path)
            if info is None or info.mod_id in seen:
                continue
            seen.add(info.mod_id)
            mods.append(ModInfo(info.mod_id, info.name, item_dir.name, info_path))
        if mods:
            found[item_dir.name] = mods
    return found


def order_mods(workshop_order: list[str], scanned: dict[str, list[ModInfo]]) -> list[ModInfo]:
    """Flatten scanned mods into the collection's load order.

    Anything downloaded but not in the collection is appended at the end rather
    than dropped, so a manually added mod is not silently lost.
    """
    ordered: list[ModInfo] = []
    for workshop_id in workshop_order:
        ordered.extend(scanned.get(workshop_id, []))
    listed = set(workshop_order)
    for workshop_id, mods in scanned.items():
        if workshop_id not in listed:
            ordered.extend(mods)
    return ordered


def format_lines(workshop_ids: list[str], mods: list[ModInfo]) -> tuple[str, str]:
    """Produce the two .env lines, semicolon-separated as PZ expects."""
    return (
        "PZ_WORKSHOP_ITEMS=" + ";".join(workshop_ids),
        "PZ_MODS=" + ";".join(mod.mod_id for mod in mods),
    )
