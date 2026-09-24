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
    for loc, items in server._get_items().items():
        for name in items:
            if ITEM in name:
                server._delete_item(loc, name)
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


def test_names_are_normalized_and_matched_case_insensitively():
    msg = server.add_item(f"  {ITEM.upper()} ", quantity="2 bags", location="pantry",
                          details="Test Brand, 16oz bag")
    assert f"Added '{ITEM}' to the pantry." in msg

    # Same food in different case sums into one row and keeps details.
    server.add_item(ITEM.title(), quantity="1 bag", location="pantry")
    item = server._get_items("pantry")["pantry"][ITEM]
    assert item["quantity"] == "3 bag"
    assert item["details"] == "Test Brand, 16oz bag"

    assert "Removed" in server.remove_item(ITEM.upper(), location="pantry")


def test_similar_item_hints():
    server.add_item(ITEM, location="pantry")

    # A plural/extra-word variant is flagged as possibly the same food.
    variant = f"golden {ITEM}"
    msg = server.add_item(variant, location="pantry")
    assert f"Similar items already in the inventory: {ITEM} (pantry)" in msg
    server.remove_item(variant, location="pantry")

    # Removing a misspelling suggests the real item and its location.
    msg = server.remove_item(ITEM[:-1], location="fridge")
    assert "Did you mean" in msg and f"{ITEM} (pantry)" in msg

    server.remove_item(ITEM, location="pantry")


def test_use_item_subtracts_partial_amounts():
    server.add_item(ITEM, quantity="10", location="pantry", low_threshold="3")

    msg = server.use_item(ITEM.upper(), "2", location="pantry")
    assert "8 left in the pantry" in msg
    assert "⚠️" in server.use_item(ITEM, "5", location="pantry")  # 3 left, at threshold

    # Mismatched units change nothing and explain the workaround.
    assert "Can't subtract" in server.use_item(ITEM, "4 oz", location="pantry")
    assert server._get_items("pantry")["pantry"][ITEM]["quantity"] == "3"

    msg = server.use_item(ITEM, "5", location="pantry")
    assert "removed" in msg
    assert ITEM not in server._get_items("pantry")["pantry"]


def test_use_item_with_units_and_bad_input():
    server.add_item(ITEM, quantity="3 bags", location="pantry")
    assert "2 bags left" in server.use_item(ITEM, "1 bag", location="pantry")
    assert "positive number" in server.use_item(ITEM, "-1", location="pantry")
    assert "not in the fridge" in server.use_item(ITEM, "1", location="fridge")
    server.remove_item(ITEM, location="pantry")


def test_details_match_other_languages():
    tag = f"测试{ITEM[-8:]}"
    server.add_item(ITEM, location="pantry", details=f"Test Brand, {tag}")

    msg = server.remove_item(tag, location="pantry")
    assert "Did you mean" in msg and f"{ITEM} (pantry)" in msg
    msg = server.use_item(tag, "1", location="fridge")
    assert f"{ITEM} (pantry)" in msg
    assert f"{ITEM} (pantry)" in server.add_item(tag, location="pantry")
    server.remove_item(tag, location="pantry")

    # Latin-script names match whole words in details, not fragments.
    assert f"{ITEM} (pantry)" in server.remove_item("test brand", location="fridge")
    assert f"{ITEM} (pantry)" not in server.remove_item("bran", location="fridge")

    server.remove_item(ITEM, location="pantry")


def test_use_item_accepts_item_name_as_unit():
    name = f"{ITEM} eggs"
    server.add_item(name, quantity="10", location="pantry")
    assert "8 eggs left" in server.use_item(name, "2 eggs", location="pantry")
    assert "7 eggs left" in server.use_item(name, "1 egg", location="pantry")
    assert "Can't subtract" in server.use_item(name, "4 oz", location="pantry")
    server.remove_item(name, location="pantry")


def test_singular_and_plural_names_match():
    plural = f"{ITEM} strawberries"
    server.add_item(plural, quantity="3", location="pantry")

    assert "2 left" in server.use_item(f"{ITEM} strawberry", "1", location="pantry")
    server.add_item(f"{ITEM} Strawberry", quantity="2", location="pantry")
    assert server._get_items("pantry")["pantry"][plural]["quantity"] == "4"

    msg = server.remove_item(f"{ITEM} strawberry", location="pantry")
    assert f"Removed '{plural}'" in msg
    assert plural not in server._get_items("pantry")["pantry"]


def test_singular_forms():
    for singular, plural in [("egg", "eggs"), ("strawberry", "strawberries"),
                             ("cherry tomato", "cherry tomatoes"), ("sauce", "sauces")]:
        assert server._singular_forms(singular) & server._singular_forms(plural)
    assert not server._singular_forms("pecan") & server._singular_forms("peanuts")
    assert server._singular_forms("hummus") & server._singular_forms("hummus")
