"""your-first-instrument — a sense of time for a model that has none.

Why time? Ask your Claude "how long have we been talking?" WITHOUT this
connected. It can only guess: no clock lives in a context window. This
server is the smallest honest fix — and the pattern generalizes to any
instrument you can imagine. See docs/adr/ for every choice made here.
"""
import os
import re
from datetime import datetime, timezone

import psycopg
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "your-first-instrument",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

# Kitchen inventory storage: two sections, fridge and freezer, backed by
# Neon Postgres (DATABASE_URL). Replaces the earlier local JSON file, which
# didn't survive restarts on Render's free tier — this does.
LOCATIONS = ("fridge", "freezer")

def _get_conn():
    return psycopg.connect(os.environ["DATABASE_URL"])

def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                location TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity TEXT NOT NULL,
                expiry TEXT NOT NULL DEFAULT '',
                made_date TEXT NOT NULL DEFAULT '',
                low_threshold TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (location, name)
            )
        """)

_init_db()

def _get_items(location: str | None = None) -> dict:
    """{location: {name: {...}}} for one location, or all locations if None."""
    query = "SELECT location, name, quantity, expiry, made_date, low_threshold FROM items"
    params = ()
    if location is not None:
        query += " WHERE location = %s"
        params = (location,)
    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    data = {loc: {} for loc in LOCATIONS}
    for loc, name, quantity, expiry, made_date, low_threshold in rows:
        data[loc][name] = {
            "quantity": quantity,
            "expiry": expiry,
            "made_date": made_date,
            "low_threshold": low_threshold,
        }
    return data

def _upsert_item(location: str, name: str, item: dict) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO items (location, name, quantity, expiry, made_date, low_threshold)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (location, name) DO UPDATE SET
                quantity = EXCLUDED.quantity,
                expiry = EXCLUDED.expiry,
                made_date = EXCLUDED.made_date,
                low_threshold = EXCLUDED.low_threshold
            """,
            (location, name, item["quantity"], item["expiry"], item["made_date"], item["low_threshold"]),
        )

def _delete_item(location: str, name: str) -> bool:
    """True if a row was deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM items WHERE location = %s AND name = %s", (location, name))
        return cur.rowcount > 0

def _check_location(location: str) -> str | None:
    """None if valid, else an error message."""
    if location not in LOCATIONS:
        return f"location must be one of {LOCATIONS}, got '{location}'."
    return None

def _leading_number(s: str) -> float | None:
    """Pull the leading number out of a quantity string like '2 eggs' -> 2.0."""
    match = re.match(r"\s*(\d+(\.\d+)?)", s)
    return float(match.group(1)) if match else None

def _parse_quantity(s: str) -> tuple[float, str] | tuple[None, None]:
    """Split a quantity string into (number, unit), e.g. '2 cups' -> (2.0, 'cups').

    (None, None) if there's no leading number to parse.
    """
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(.*)", s)
    if not match:
        return None, None
    return float(match.group(1)), match.group(2).strip()

def _format_number(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n)

def _is_low(item: dict) -> bool:
    """True if quantity has dropped to or below the item's low_threshold."""
    threshold = _leading_number(item.get("low_threshold", ""))
    quantity = _leading_number(item.get("quantity", ""))
    return threshold is not None and quantity is not None and quantity <= threshold

@mcp.tool()
def current_time() -> str:
    """The current date and time (UTC and local)."""
    now = datetime.now(timezone.utc)
    return f"UTC: {now.isoformat()} · local: {datetime.now().isoformat()}"

@mcp.tool()
def seconds_since(iso_timestamp: str) -> str:
    """Seconds elapsed since an ISO timestamp (e.g. '2026-09-10T17:15:00')."""
    then = datetime.fromisoformat(iso_timestamp)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    return f"{delta.total_seconds():.0f} seconds ({delta})"

@mcp.tool()
def add_item(
    name: str,
    quantity: str = "1",
    expiry: str = "",
    made_date: str = "",
    low_threshold: str = "",
    location: str = "fridge",
) -> str:
    """Add an item to the fridge or freezer.

    If the item is already there and both the existing and new quantities
    start with a number (e.g. "3" + "2" -> "5", "1 cup" + "2 cups" -> "3
    cups"), the quantities are summed automatically. Otherwise (item not
    present yet, or either quantity isn't numeric, e.g. "half a carton")
    this call overwrites the quantity instead of summing it — it does not
    know how to convert "half a carton" + "2" into a total. In that case,
    call list_inventory first, work out the new total yourself (using
    whatever real-world conversion applies), and pass that total here as
    the quantity. expiry, made_date, and low_threshold always overwrite,
    regardless.

    location is "fridge" or "freezer".

    expiry and made_date are optional, format YYYY-MM-DD. made_date is for
    things you cooked or prepped yourself (leftovers, meal components) — use
    it instead of expiry when there's no package date to go by.

    low_threshold is optional, e.g. "2" or "2 cups" — when quantity drops to
    or below this amount, the item is flagged as running low in
    list_inventory and check_low_stock. Leave blank to skip the low-stock
    check for this item.
    """
    if error := _check_location(location):
        return error
    existing = _get_items(location)[location].get(name)
    if existing is not None:
        old_number, old_unit = _parse_quantity(existing["quantity"])
        new_number, new_unit = _parse_quantity(quantity)
        if old_number is not None and new_number is not None:
            unit = new_unit or old_unit
            quantity = f"{_format_number(old_number + new_number)} {unit}".strip()
    item = {
        "quantity": quantity,
        "expiry": expiry,
        "made_date": made_date,
        "low_threshold": low_threshold,
    }
    _upsert_item(location, name, item)
    items = _get_items(location)[location]
    msg = f"Added '{name}' to the {location}. Current {location}: {items}"
    if _is_low(item):
        msg += f"\n⚠️ '{name}' is already at or below its low-stock threshold."
    return msg

@mcp.tool()
def remove_item(name: str, location: str = "fridge") -> str:
    """Remove an item from the fridge or freezer (used up or thrown out).

    location is "fridge" or "freezer".
    """
    if error := _check_location(location):
        return error
    if not _delete_item(location, name):
        return f"'{name}' is not in the {location}."
    items = _get_items(location)[location]
    return f"Removed '{name}' from the {location}. Current {location}: {items}"

@mcp.tool()
def list_inventory(location: str = "all") -> str:
    """The current inventory, flagging anything running low.

    location is "fridge", "freezer", or "all" (default) for both.
    """
    if location != "all" and (error := _check_location(location)):
        return error
    data = _get_items(None if location == "all" else location)
    sections = data if location == "all" else {location: data[location]}
    if not any(sections.values()):
        return "Empty." if location == "all" else f"The {location} is empty."
    lines = []
    for loc, items in sections.items():
        if not items:
            lines.append(f"{loc}: empty")
            continue
        low = [name for name, item in items.items() if _is_low(item)]
        line = f"{loc}: {items}"
        if low:
            line += f" ⚠️ Running low: {', '.join(low)}"
        lines.append(line)
    return "\n".join(lines)

@mcp.tool()
def check_low_stock(location: str = "all") -> str:
    """Just the items that are at or below their low_threshold, if any.

    location is "fridge", "freezer", or "all" (default) for both.
    """
    if location != "all" and (error := _check_location(location)):
        return error
    data = _get_items(None if location == "all" else location)
    sections = data if location == "all" else {location: data[location]}
    low = {
        f"{loc}/{name}": item
        for loc, items in sections.items()
        for name, item in items.items()
        if _is_low(item)
    }
    if not low:
        return "Nothing is running low."
    return f"Running low: {low}"

@mcp.tool()
def my_tool() -> str:
    """YOURS. Rename it, give it a real purpose, make the model reach
    something it couldn't before. (Track ideas: docs/TRACKS.md)"""
    return "Not built yet — that's the point. Edit server.py."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
