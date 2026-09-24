"""fridge-inventory — a food inventory a model has no way to see.

An AI model only knows what's in its context window — it can't see what's
actually in your kitchen. This server hands it that: add, remove, and list
food items across any storage location (fridge, freezer, pantry, ...), with
low-stock flags. See docs/adr/ for every choice made here.
"""
import difflib
import os
import re

import psycopg
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "fridge-inventory",
    instructions=(
        "A food inventory: what food is on hand, where it's stored, how much "
        "is left, and when it expires. Each item has a free-form storage "
        "location — commonly \"fridge\", \"freezer\", or \"pantry\", but any "
        "location works."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

# Food inventory storage, backed by Neon Postgres (DATABASE_URL). Replaces the
# earlier local JSON file, which didn't survive restarts on Render's free
# tier — this does. Location is a free-form attribute of each item. Every
# location ever used is remembered in the locations table, so it sticks
# around (and shows up as empty) after its last item is removed.
COMMON_LOCATIONS = ("fridge", "freezer", "pantry")

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
        conn.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS details TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE TABLE IF NOT EXISTS locations (name TEXT PRIMARY KEY)")
        # Backfill: fridge and freezer always existed before locations were
        # free-form, plus any location already holding items.
        conn.execute("""
            INSERT INTO locations (name)
            SELECT 'fridge' UNION SELECT 'freezer' UNION SELECT DISTINCT location FROM items
            ON CONFLICT DO NOTHING
        """)

_init_db()

def _get_items(location: str | None = None) -> dict:
    """{location: {name: {...}}} for one location, or all locations if None.

    Every known location appears (empty ones as {}), common locations first.
    A specific location that isn't known returns {}.
    """
    query = "SELECT location, name, quantity, details, expiry, made_date, low_threshold FROM items"
    params = ()
    if location is not None:
        query += " WHERE location = %s"
        params = (location,)
    with _get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    known = _get_locations()
    data = {loc: {} for loc in known if location is None or loc == location}
    for loc, name, quantity, details, expiry, made_date, low_threshold in rows:
        data.setdefault(loc, {})[name] = {
            "quantity": quantity,
            "details": details,
            "expiry": expiry,
            "made_date": made_date,
            "low_threshold": low_threshold,
        }
    return {loc: data[loc] for loc in sorted(data, key=_location_sort_key)}

def _location_sort_key(location: str) -> tuple[int, str]:
    if location in COMMON_LOCATIONS:
        return (COMMON_LOCATIONS.index(location), "")
    return (len(COMMON_LOCATIONS), location)

def _get_locations() -> list[str]:
    """Every known location, including ones that are currently empty."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT name FROM locations").fetchall()
    return sorted((loc for (loc,) in rows), key=_location_sort_key)

def _upsert_item(location: str, name: str, item: dict) -> None:
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO locations (name) VALUES (%s) ON CONFLICT DO NOTHING", (location,)
        )
        conn.execute(
            """
            INSERT INTO items (location, name, quantity, details, expiry, made_date, low_threshold)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (location, name) DO UPDATE SET
                quantity = EXCLUDED.quantity,
                details = EXCLUDED.details,
                expiry = EXCLUDED.expiry,
                made_date = EXCLUDED.made_date,
                low_threshold = EXCLUDED.low_threshold
            """,
            (
                location, name, item["quantity"], item["details"],
                item["expiry"], item["made_date"], item["low_threshold"],
            ),
        )

def _delete_location(location: str) -> bool:
    """True if the location was deleted. Only deletes it if it holds no items."""
    with _get_conn() as conn:
        cur = conn.execute(
            """
            DELETE FROM locations WHERE name = %s
            AND NOT EXISTS (SELECT 1 FROM items WHERE location = %s)
            """,
            (location, location),
        )
        return cur.rowcount > 0

def _delete_item(location: str, name: str) -> bool:
    """True if a row was deleted. name matches case-insensitively."""
    with _get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM items WHERE location = %s AND lower(name) = lower(%s)", (location, name)
        )
        return cur.rowcount > 0

def _normalize_name(name: str) -> str:
    """Trim, collapse whitespace, lowercase: "  Golden  Raisins " -> "golden raisins"."""
    return " ".join(name.split()).lower()

def _find_item(items: dict, name: str) -> tuple[str, dict] | tuple[None, None]:
    """(stored name, item) for a case-insensitive match in one location's items."""
    for stored, item in items.items():
        if _normalize_name(stored) == name:
            return stored, item
    return None, None

def _similar_items(name: str, cutoff: float, skip: tuple[str, str] | None = None) -> list[str]:
    """Items anywhere that look like the same food as name, as "name (location)".

    Similar means one name's words contain the other's ("raisins" vs "golden
    raisins") or the spelling is close ("rasins" vs "raisins"). skip is a
    (location, name) pair to leave out.
    """
    words = set(name.split())
    matches = []
    for loc, items in _get_items().items():
        for stored in items:
            other = _normalize_name(stored)
            if (loc, other) == skip:
                continue
            other_words = set(other.split())
            if (
                words <= other_words
                or other_words <= words
                or difflib.SequenceMatcher(None, name, other).ratio() >= cutoff
            ):
                matches.append(f"{stored} ({loc})")
    return matches

def _normalize_location(location: str) -> str:
    return location.strip().lower()

def _check_location(location: str) -> str | None:
    """None if valid (already normalized), else an error message."""
    if not location:
        return "location can't be empty, e.g. \"fridge\", \"freezer\", or \"pantry\"."
    if location == "all":
        return "\"all\" is reserved for listing every location; pick a specific location."
    return None

def _format_locations(locations: list[str]) -> str:
    return ", ".join(locations) if locations else "(none yet)"

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
def add_item(
    name: str,
    quantity: str = "1",
    expiry: str = "",
    made_date: str = "",
    low_threshold: str = "",
    location: str = "fridge",
    details: str = "",
) -> str:
    """Add a food item to the inventory at a storage location.

    name is the short, generic, everyday name you'd say out loud: "raisins",
    "cheddar", "broccoli", "chinese sausage" — not the brand or package
    name, and not "frozen broccoli" (the location already says that). Add
    a distinguishing word only when two different things would otherwise
    share a name ("chocolate whey protein" vs "unflavored whey protein").
    It's trimmed and lowercased, and matches existing items regardless of
    case. Put brand, variety, and package size in details instead, e.g.
    details="Trader Joe's California Golden, 16oz bag". details is kept
    when left blank on a later add.

    The response lists similar items already in the inventory (e.g. adding
    "raisin" when "raisins" exists). If one is the same food, remove this
    new item and add to that one instead, so there's one row per food.

    If the item is already there and both the existing and new quantities
    start with a number (e.g. "3" + "2" -> "5", "1 cup" + "2 cups" -> "3
    cups"), the quantities are summed automatically. Otherwise (item not
    present yet, or either quantity isn't numeric, e.g. "half a carton")
    this call overwrites the quantity instead of summing it — it does not
    know how to convert "half a carton" + "2" into a total. In that case,
    call list_inventory first, work out the new total yourself (using
    whatever real-world conversion applies), and pass that total here as
    the quantity. Keep quantity to the count and unit ("2 bags", "8 oz") and
    put pack sizes in details, so later adds can be summed. expiry,
    made_date, and low_threshold always overwrite, regardless.

    location is any storage location, e.g. "fridge" (default), "freezer",
    or "pantry" — these are common examples, not a fixed list. It's
    trimmed and lowercased. A location that doesn't exist yet is created,
    and the response says so and lists the existing locations, so double
    check it wasn't a typo (if it was, move the item and call
    remove_location on the typo).

    expiry and made_date are optional, format YYYY-MM-DD. made_date is for
    things you cooked or prepped yourself (leftovers, meal components) — use
    it instead of expiry when there's no package date to go by.

    low_threshold is optional, e.g. "2" or "2 cups" — when quantity drops to
    or below this amount, the item is flagged as running low in
    list_inventory and check_low_stock. Leave blank to skip the low-stock
    check for this item.
    """
    location = _normalize_location(location)
    if error := _check_location(location):
        return error
    name = _normalize_name(name)
    if not name:
        return "name can't be empty."
    existing_locations = _get_locations()
    stored_name, existing = _find_item(_get_items(location).get(location, {}), name)
    if existing is not None:
        name = stored_name
        details = details or existing["details"]
        old_number, old_unit = _parse_quantity(existing["quantity"])
        new_number, new_unit = _parse_quantity(quantity)
        if old_number is not None and new_number is not None:
            unit = new_unit or old_unit
            quantity = f"{_format_number(old_number + new_number)} {unit}".strip()
    item = {
        "quantity": quantity,
        "details": details,
        "expiry": expiry,
        "made_date": made_date,
        "low_threshold": low_threshold,
    }
    _upsert_item(location, name, item)
    items = _get_items(location)[location]
    msg = f"Added '{name}' to the {location}. Current {location}: {items}"
    if location not in existing_locations:
        msg += (
            f"\n🆕 Created new location '{location}'. Existing locations: "
            f"{_format_locations(existing_locations)}. If '{location}' was a "
            "typo, remove the item, add it again under the right location, and "
            f"call remove_location('{location}')."
        )
    if existing is None and (similar := _similar_items(name, 0.85, skip=(location, name))):
        msg += (
            f"\n🔎 Similar items already in the inventory: {', '.join(similar)}. "
            f"If '{name}' is the same food, remove it and add to that item instead."
        )
    if _is_low(item):
        msg += f"\n⚠️ '{name}' is already at or below its low-stock threshold."
    return msg

@mcp.tool()
def remove_item(name: str, location: str = "fridge") -> str:
    """Remove a food item from the inventory (used up or thrown out).

    name matches regardless of case; if nothing matches, the response
    suggests similar items and where they are.

    location is the storage location the item is in, e.g. "fridge"
    (default), "freezer", or "pantry" — any location string works. It's
    trimmed and lowercased. The location is kept even after its last item
    is removed; use remove_location to delete an empty location.
    """
    location = _normalize_location(location)
    if error := _check_location(location):
        return error
    name = _normalize_name(name)
    if not _delete_item(location, name):
        msg = f"'{name}' is not in the {location}."
        if similar := _similar_items(name, 0.6):
            msg += f" Did you mean: {', '.join(similar)}?"
        else:
            msg += f" Existing locations: {_format_locations(_get_locations())}."
        return msg
    items = _get_items(location).get(location)
    if not items:
        return f"Removed '{name}' from the {location}. The {location} is now empty."
    return f"Removed '{name}' from the {location}. Current {location}: {items}"

@mcp.tool()
def remove_location(location: str) -> str:
    """Delete an empty storage location, e.g. one created by a typo.

    Only works on a location with no items in it — remove or move its items
    first. Locations otherwise stick around after they empty out.
    """
    location = _normalize_location(location)
    if error := _check_location(location):
        return error
    locations = _get_locations()
    if location not in locations:
        return f"There's no location '{location}'. Existing locations: {_format_locations(locations)}."
    if not _delete_location(location):
        items = _get_items(location).get(location, {})
        return f"The {location} isn't empty, so it wasn't removed. It holds: {', '.join(items)}."
    return f"Removed location '{location}'. Existing locations: {_format_locations(_get_locations())}."

@mcp.tool()
def list_inventory(location: str = "all") -> str:
    """The current food inventory, grouped by location, flagging anything
    running low.

    location is "all" (default) for every location, or any specific
    location to filter to it, e.g. "fridge", "freezer", or "pantry".
    """
    location = _normalize_location(location) or "all"
    sections = _get_items(None if location == "all" else location)
    if not sections:
        if location == "all":
            return "The inventory is empty."
        return (
            f"There's no location '{location}'. Existing locations: "
            f"{_format_locations(_get_locations())}."
        )
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
    """Just the food items that are at or below their low_threshold, if any,
    grouped by location.

    location is "all" (default) for every location, or any specific
    location to filter to it, e.g. "fridge", "freezer", or "pantry".
    """
    location = _normalize_location(location) or "all"
    sections = _get_items(None if location == "all" else location)
    if not sections and location != "all":
        return (
            f"There's no location '{location}'. Existing locations: "
            f"{_format_locations(_get_locations())}."
        )
    low = {
        loc: {name: item for name, item in items.items() if _is_low(item)}
        for loc, items in sections.items()
    }
    low = {loc: items for loc, items in low.items() if items}
    if not low:
        return "Nothing is running low."
    return "Running low:\n" + "\n".join(f"{loc}: {items}" for loc, items in low.items())

@mcp.tool()
def my_tool() -> str:
    """YOURS. Rename it, give it a real purpose, make the model reach
    something it couldn't before. (Track ideas: docs/TRACKS.md)"""
    return "Not built yet — that's the point. Edit server.py."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
