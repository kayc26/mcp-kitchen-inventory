"""Tool list and leftover-expiry tests. Runs against the database in DATABASE_URL.

Only touches test-named items in "pantry" and removes them afterwards; the
backfill test runs inside a transaction that is rolled back.
"""
import asyncio
import uuid

import pytest

import server

ITEM = f"test-soup-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for loc, items in server._get_items().items():
        for name in items:
            if ITEM in name:
                server._delete_item(loc, name)


def test_exposed_tools():
    assert server.mcp.name == "kitchen-inventory"
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert names == {
        "list_inventory", "add_item", "use_item",
        "remove_item", "remove_location", "check_low_stock", "get_expiring",
    }
    assert not hasattr(server, "my_tool")


def test_leftover_expiry():
    assert server._leftover_expiry("2026-09-25") == "2026-09-29"
    assert server._leftover_expiry("2026-02-26") == "2026-03-02"
    assert server._leftover_expiry(" 2026-12-30 ") == "2027-01-03"
    assert server._leftover_expiry("") == ""
    assert server._leftover_expiry("last tuesday") == ""


def test_made_date_without_expiry_gets_four_days():
    msg = server.add_item(ITEM, location="pantry", made_date="2026-09-25")
    assert "Expiry set to 2026-09-29" in msg
    assert server._get_items("pantry")["pantry"][ITEM]["expiry"] == "2026-09-29"


def test_given_expiry_wins():
    msg = server.add_item(ITEM, location="pantry", made_date="2026-09-25", expiry="2026-10-10")
    assert "Expiry set to" not in msg
    assert server._get_items("pantry")["pantry"][ITEM]["expiry"] == "2026-10-10"


def test_no_made_date_or_bad_made_date_leaves_expiry_blank():
    server.add_item(ITEM, location="pantry")
    assert server._get_items("pantry")["pantry"][ITEM]["expiry"] == ""
    msg = server.add_item(ITEM, location="pantry", made_date="yesterday")
    assert "Expiry set to" not in msg
    item = server._get_items("pantry")["pantry"][ITEM]
    assert item["expiry"] == "" and item["made_date"] == "yesterday"


def test_backfill_sets_missing_expiry_only():
    with server._get_conn() as conn:
        rows = [
            (f"{ITEM}-a", "2026-09-20", ""),
            (f"{ITEM}-b", "2026-09-20", "2026-12-01"),
            (f"{ITEM}-c", "", ""),
            (f"{ITEM}-d", "not a date", ""),
        ]
        for name, made_date, expiry in rows:
            conn.execute(
                "INSERT INTO items (location, name, quantity, expiry, made_date) "
                "VALUES ('pantry', %s, '1', %s, %s)",
                (name, expiry, made_date),
            )
        assert server._backfill_leftover_expiry(conn) >= 1
        got = dict(conn.execute(
            "SELECT name, expiry FROM items WHERE location = 'pantry' AND name LIKE %s",
            (f"{ITEM}-%",),
        ).fetchall())
        assert got == {
            f"{ITEM}-a": "2026-09-24",
            f"{ITEM}-b": "2026-12-01",
            f"{ITEM}-c": "",
            f"{ITEM}-d": "",
        }
        assert server._backfill_leftover_expiry(conn) == 0
        conn.rollback()
