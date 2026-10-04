"""update_item tests. Runs against the database in DATABASE_URL.

Only touches test-named items in "pantry", "freezer", and a throwaway
location, and removes them afterwards; locations created here are removed too.
"""
import uuid

import pytest

import server

ITEM = f"test-stew-{uuid.uuid4().hex[:8]}"
NEW_LOCATION = f"test-cellar-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    locations_before = server._get_locations()
    yield
    for loc, items in server._get_items().items():
        for name in items:
            if ITEM in name:
                server._delete_item(loc, name)
    for loc in set(server._get_locations()) - set(locations_before):
        server._delete_location(loc)


def _add(name: str = ITEM, location: str = "pantry") -> None:
    server.add_item(name, quantity="2 cups", location=location, details="beef",
                    expiry="2026-10-10", made_date="2026-10-01", low_threshold="1 cup")


def _stored(name: str = ITEM, location: str = "pantry") -> dict | None:
    return server._get_items(location).get(location, {}).get(name)


@pytest.mark.parametrize("field, value, expected", [
    ("details", "beef and barley", "beef and barley"),
    ("quantity", "1 cup", "1 cup"),
    ("expiry", " 2026-10-20 ", "2026-10-20"),
    ("made_date", "2026-10-02", "2026-10-02"),
    ("low_threshold", "2 cups", "2 cups"),
])
def test_each_field_updates(field, value, expected):
    _add()
    before = dict(_stored())
    result = server.update_item(ITEM.upper(), field, value, location=" Pantry ")
    assert result["field"] == field
    assert result["previous"] == before[field]
    assert result["item"] == {"name": ITEM, "location": "pantry", **before, field: expected}
    assert _stored() == {**before, field: expected}


def test_rename():
    _add()
    before = dict(_stored())
    result = server.update_item(ITEM, "name", f"  {ITEM.upper()}  Soup ", location="pantry")
    new_name = f"{ITEM} soup"
    assert result["previous"] == ITEM
    assert result["item"] == {"name": new_name, "location": "pantry", **before}
    assert _stored() is None
    assert _stored(new_name) == before


def test_move_to_existing_location_keeps_expiry():
    _add()
    before = dict(_stored())
    result = server.update_item(ITEM, "location", "Freezer", location="pantry")
    assert result["previous"] == "pantry"
    assert result["item"] == {"name": ITEM, "location": "freezer", **before}
    assert "created_location" not in result
    assert _stored() is None
    assert _stored(location="freezer") == before


def test_move_to_new_location_creates_it():
    _add()
    result = server.update_item(ITEM, "location", NEW_LOCATION.upper(), location="pantry")
    assert result["item"]["location"] == NEW_LOCATION
    assert f"Created new location '{NEW_LOCATION}'" in result["created_location"]
    assert NEW_LOCATION in server._get_locations()
    assert _stored(location=NEW_LOCATION) is not None


def test_made_date_change_leaves_expiry_untouched():
    _add()
    result = server.update_item(ITEM, "made_date", "2026-10-05", location="pantry")
    assert result["item"]["expiry"] == "2026-10-10"
    assert _stored()["expiry"] == "2026-10-10"


@pytest.mark.parametrize("field", ["expiry", "made_date"])
@pytest.mark.parametrize("value", ["2026-10-32", "10/05/2026", "2026-1-5", "tomorrow"])
def test_invalid_date_rejected(field, value):
    _add()
    before = dict(_stored())
    result = server.update_item(ITEM, field, value, location="pantry")
    assert "YYYY-MM-DD" in result["error"]
    assert _stored() == before


@pytest.mark.parametrize("field", ["details", "expiry", "made_date", "low_threshold"])
def test_empty_value_clears_optional_fields(field):
    _add()
    previous = _stored()[field]
    result = server.update_item(ITEM, field, "  ", location="pantry")
    assert result["previous"] == previous
    assert result["item"][field] == ""
    assert _stored()[field] == ""


@pytest.mark.parametrize("field", ["name", "location", "quantity"])
def test_required_fields_cant_be_cleared(field):
    _add()
    before = dict(_stored())
    result = server.update_item(ITEM, field, "", location="pantry")
    assert "can't be cleared" in result["error"]
    assert _stored() == before


def test_rename_into_existing_item_rejected():
    other = f"{ITEM} chili"
    _add()
    server.add_item(other, quantity="5", location="pantry")
    before = server._get_items("pantry")["pantry"]
    # Singular/plural counts as the same name, like everywhere else.
    result = server.update_item(ITEM, "name", f"{other}s", location="pantry")
    assert "already" in result["error"]
    assert server._get_items("pantry")["pantry"] == before


def test_move_into_existing_item_rejected():
    _add()
    _add(location=NEW_LOCATION)
    server.update_item(ITEM, "quantity", "9", location=NEW_LOCATION)
    pantry, cellar = _stored(), _stored(location=NEW_LOCATION)
    result = server.update_item(ITEM, "location", NEW_LOCATION, location="pantry")
    assert "already" in result["error"]
    assert _stored() == pantry
    assert _stored(location=NEW_LOCATION) == cellar


def test_rename_to_own_plural_is_not_a_collision():
    _add()
    result = server.update_item(ITEM, "name", f"{ITEM}s", location="pantry")
    assert result["item"]["name"] == f"{ITEM}s"


def test_no_match_suggests_similar_items():
    _add()
    result = server.update_item(ITEM[:-1], "quantity", "1", location="fridge")
    assert "not in the fridge" in result["error"]
    assert f"Did you mean: {ITEM} (pantry)" in result["error"]


def test_bad_field_and_location_rejected():
    _add()
    before = dict(_stored())
    assert "field must be one of" in server.update_item(ITEM, "colour", "red", location="pantry")["error"]
    assert "reserved" in server.update_item(ITEM, "location", "all", location="pantry")["error"]
    assert _stored() == before
