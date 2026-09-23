"""Free-form location tests. Runs against the database in DATABASE_URL.

Only touches test-named items in "pantry" and a throwaway location, and
removes them afterwards; fridge and freezer are checked to be unchanged.
"""
import uuid

import pytest

import server

ITEM = f"test-rice-{uuid.uuid4().hex[:8]}"
NEW_LOCATION = f"test-cellar-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup_and_guard_fridge_freezer():
    before = {loc: server._get_items(loc).get(loc, {}) for loc in ("fridge", "freezer")}
    locations_before = server._get_locations()
    yield
    server._delete_item("pantry", ITEM)
    server._delete_item(NEW_LOCATION, ITEM)
    for loc in set(server._get_locations()) - set(locations_before):
        server._delete_location(loc)
    after = {loc: server._get_items(loc).get(loc, {}) for loc in ("fridge", "freezer")}
    assert after == before


def test_pantry_item_add_list_remove():
    msg = server.add_item(ITEM, quantity="2 bags", location="  Pantry ")
    assert f"Added '{ITEM}' to the pantry." in msg

    everything = server.list_inventory()
    pantry_line = next(line for line in everything.splitlines() if line.startswith("pantry:"))
    assert ITEM in pantry_line

    pantry_only = server.list_inventory("pantry")
    assert pantry_only.startswith("pantry:")
    assert ITEM in pantry_only
    assert "fridge:" not in pantry_only and "freezer:" not in pantry_only

    msg = server.remove_item(ITEM, location="PANTRY")
    assert f"Removed '{ITEM}' from the pantry." in msg
    assert ITEM not in server.list_inventory("pantry")
    assert "pantry" in server._get_locations()


def test_new_location_notice():
    existing = server._get_locations()
    msg = server.add_item(ITEM, location=NEW_LOCATION.upper())
    assert f"Created new location '{NEW_LOCATION}'" in msg
    for loc in existing:
        assert loc in msg

    # A second add to the now-existing location doesn't repeat the notice.
    msg = server.add_item(ITEM, location=NEW_LOCATION)
    assert "Created new location" not in msg

    assert "Removed" in server.remove_item(ITEM, location=NEW_LOCATION)


def test_empty_location_persists_until_removed():
    server.add_item(ITEM, location=NEW_LOCATION)
    assert "isn't empty" in server.remove_location(NEW_LOCATION)

    server.remove_item(ITEM, location=NEW_LOCATION)
    assert f"{NEW_LOCATION}: empty" in server.list_inventory()
    assert server.list_inventory(NEW_LOCATION) == f"{NEW_LOCATION}: empty"
    assert "Created new location" not in server.add_item(ITEM, location=NEW_LOCATION)
    server.remove_item(ITEM, location=NEW_LOCATION)

    assert f"Removed location '{NEW_LOCATION}'" in server.remove_location(NEW_LOCATION)
    assert NEW_LOCATION not in server._get_locations()
    assert "no location" in server.list_inventory(NEW_LOCATION)


def test_rejects_empty_and_reserved_locations():
    assert "can't be empty" in server.add_item(ITEM, location="   ")
    assert "reserved" in server.add_item(ITEM, location="all")
