"""kitchen-inventory — a food inventory a model has no way to see.

An AI model only knows what's in its context window — it can't see what's
actually in your kitchen. This server hands it that: add, remove, and list
food items across any storage location (fridge, freezer, pantry, ...), with
low-stock flags. See docs/adr/ for every choice made here.
"""
import datetime
import difflib
import os
import re
from zoneinfo import ZoneInfo

import psycopg
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse, PlainTextResponse

import kitchen_auth

# Sign-in is required (ADR-0002, task 15). Exits here if an auth env var is
# missing, before anything else runs.
AUTH_CONFIG = kitchen_auth.load_config()

mcp = FastMCP(
    "kitchen-inventory",
    instructions=(
        "A food inventory: what food is on hand, where it's stored, how much "
        "is left, and when it expires. Each item has a free-form storage "
        "location — commonly \"fridge\", \"freezer\", or \"pantry\", but any "
        "location works."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
    **kitchen_auth.fastmcp_auth_kwargs(AUTH_CONFIG),
)
kitchen_auth.register_pages(mcp, AUTH_CONFIG)

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

def _update_field(location: str, name: str, field: str, value: str) -> bool:
    """Set one column on one item in a single transaction, creating the
    location first if field is "location". False (and nothing changed) if
    that would collide with another item's (location, name)."""
    assert field in UPDATABLE_FIELDS
    try:
        with _get_conn() as conn:
            if field == "location":
                conn.execute(
                    "INSERT INTO locations (name) VALUES (%s) ON CONFLICT DO NOTHING", (value,)
                )
            conn.execute(
                f"UPDATE items SET {field} = %s WHERE location = %s AND name = %s",
                (value, location, name),
            )
    except psycopg.errors.UniqueViolation:
        return False
    return True

def _normalize_name(name: str) -> str:
    """Trim, collapse whitespace, lowercase: "  Golden  Raisins " -> "golden raisins"."""
    return " ".join(name.split()).lower()

def _singular_forms(name: str) -> set[str]:
    """name plus the singulars its last word could be: "cherry tomatoes" ->
    {"cherry tomatoes", "cherry tomatoe", "cherry tomato"}. Two names are the
    same food if their forms overlap, so "egg" matches "eggs" and
    "strawberry" matches "strawberries"."""
    forms = {name}
    if name.endswith("ies"):
        forms.add(name[:-3] + "y")
    if name.endswith("es"):
        forms.add(name[:-2])
    if name.endswith("s") and not name.endswith("ss"):
        forms.add(name[:-1])
    return forms

def _find_item(items: dict, name: str) -> tuple[str, dict] | tuple[None, None]:
    """(stored name, item) in one location's items, ignoring case and
    singular/plural. An exact match wins over a plural match."""
    forms = _singular_forms(name)
    match = (None, None)
    for stored, item in items.items():
        other = _normalize_name(stored)
        if other == name:
            return stored, item
        if forms & _singular_forms(other):
            match = (stored, item)
    return match

def _similar_items(name: str, cutoff: float, skip: tuple[str, str] | None = None) -> list[str]:
    """Items anywhere that look like the same food as name, as "name (location)".

    Similar means one name's words contain the other's ("raisins" vs "golden
    raisins"), the spelling is close ("rasins" vs "raisins"), or name shows
    up in the item's details ("皮蛋" matches century eggs, details "皮蛋").
    skip is a (location, name) pair to leave out.
    """
    words = set(name.split())
    matches = []
    for loc, items in _get_items().items():
        for stored, item in items.items():
            other = _normalize_name(stored)
            if (loc, other) == skip:
                continue
            other_words = set(other.split())
            if (
                words <= other_words
                or other_words <= words
                or difflib.SequenceMatcher(None, name, other).ratio() >= cutoff
                or _in_details(name, item["details"])
            ):
                matches.append(f"{stored} ({loc})")
    return matches

def _in_details(name: str, details: str) -> bool:
    """True if name appears in details: as whole words for Latin-script names,
    as a substring otherwise (Chinese has no spaces between words)."""
    details = details.lower()
    if name.isascii():
        return set(name.split()) <= set(re.findall(r"\w+", details))
    return name in details

def _not_found(name: str, location: str) -> str:
    """'name' isn't in location, plus similar items anywhere or, failing
    that, the existing locations."""
    msg = f"'{name}' is not in the {location}."
    if similar := _similar_items(name, 0.6):
        msg += f" Did you mean: {', '.join(similar)}?"
    else:
        msg += f" Existing locations: {_format_locations(_get_locations())}."
    return msg

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

def _same_unit(stored: str, used: str, name: str) -> bool:
    """Units match ignoring plurals ("bag" == "bags"). A bare used number
    matches any stored unit. A bare stored count only matches a bare number
    or the item itself as the unit ("2 eggs" from "10" eggs, not "4 oz")."""
    def singular(word: str) -> str:
        return word.lower().rstrip("s")
    if not used:
        return True
    if not stored:
        return singular(used) in {singular(w) for w in name.split()}
    return singular(stored) == singular(used)

def _format_number(n: float) -> str:
    return str(int(n)) if n == int(n) else str(n)

UPDATABLE_FIELDS = ("name", "details", "location", "quantity", "expiry", "made_date", "low_threshold")
REQUIRED_FIELDS = ("name", "location", "quantity")
DATE_FIELDS = ("expiry", "made_date")

def _is_date(s: str) -> bool:
    """True if s is a real date written exactly YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return False
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        return False
    return True

LEFTOVER_DAYS = 4

# "Today" is the date in Eastern time, not the host's clock: Render runs in
# UTC, which would roll over to tomorrow at 8pm.
LOCAL_TZ = ZoneInfo("America/New_York")

def _today() -> datetime.date:
    return datetime.datetime.now(LOCAL_TZ).date()

def _leftover_expiry(made_date: str) -> str:
    """made_date + LEFTOVER_DAYS as YYYY-MM-DD, or "" if made_date isn't a
    YYYY-MM-DD date."""
    try:
        made = datetime.datetime.strptime(made_date.strip(), "%Y-%m-%d").date()
    except ValueError:
        return ""
    return (made + datetime.timedelta(days=LEFTOVER_DAYS)).isoformat()

def _backfill_leftover_expiry(conn: psycopg.Connection) -> int:
    """Give rows with a made_date but no expiry expiry = made_date + 4 days.
    Returns how many rows changed; unparseable made_dates are left alone."""
    rows = conn.execute(
        "SELECT location, name, made_date FROM items WHERE expiry = '' AND made_date <> ''"
    ).fetchall()
    changed = 0
    for location, name, made_date in rows:
        if expiry := _leftover_expiry(made_date):
            conn.execute(
                "UPDATE items SET expiry = %s WHERE location = %s AND name = %s AND expiry = ''",
                (expiry, location, name),
            )
            changed += 1
    return changed

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
    case or singular/plural ("egg" adds to "eggs"). Put brand, variety, and package size in details instead, e.g.
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
    check it wasn't a typo (if it was, move the item with update_item and call
    remove_location on the typo).

    expiry and made_date are optional, format YYYY-MM-DD. made_date is for
    things you cooked or prepped yourself (leftovers, meal components) — use
    it instead of expiry when there's no package date to go by. When
    made_date is given and expiry is blank, expiry is set to made_date + 4
    days, and the response says so; a given expiry always wins.

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
    leftover_expiry = "" if expiry else _leftover_expiry(made_date)
    expiry = expiry or leftover_expiry
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
    if leftover_expiry:
        msg += f"\n🗓️ Expiry set to {expiry} (made_date {made_date} + {LEFTOVER_DAYS} days)."
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
    """Remove a food item from the inventory entirely (used up or thrown
    out). To subtract part of an item, use use_item instead.

    name matches regardless of case or singular/plural ("egg" finds
    "eggs"); if nothing matches, the response suggests similar items and where they are.

    location is the storage location the item is in, e.g. "fridge"
    (default), "freezer", or "pantry" — any location string works. It's
    trimmed and lowercased. The location is kept even after its last item
    is removed; use remove_location to delete an empty location.
    """
    location = _normalize_location(location)
    if error := _check_location(location):
        return error
    name = _normalize_name(name)
    stored_name, _ = _find_item(_get_items(location).get(location, {}), name)
    if stored_name is None or not _delete_item(location, stored_name):
        return _not_found(name, location)
    name = stored_name
    items = _get_items(location).get(location)
    if not items:
        return f"Removed '{name}' from the {location}. The {location} is now empty."
    return f"Removed '{name}' from the {location}. Current {location}: {items}"

@mcp.tool()
def use_item(name: str, quantity: str, location: str = "fridge") -> str:
    """Subtract an amount of a food item that was partly used, e.g. 2 of 10
    eggs for a recipe. Use remove_item instead when it's all used up.

    quantity is the amount used, e.g. "2" or "1 cup", in the same unit the
    item is stored in (see list_inventory). If the units don't match (item
    stored as "1 bag", used "4 oz") or the stored quantity isn't a number,
    nothing changes: work out what's left, then use update_item with field
    quantity and the remaining amount instead. If what's used
    reaches or exceeds what's stored, the item is removed.

    name matches regardless of case or singular/plural ("egg" finds
    "eggs"); if nothing matches, the response suggests similar items and where they are. location is the storage
    location the item is in, e.g. "fridge" (default), "freezer", or
    "pantry".
    """
    location = _normalize_location(location)
    if error := _check_location(location):
        return error
    name = _normalize_name(name)
    stored_name, item = _find_item(_get_items(location).get(location, {}), name)
    if item is None:
        msg = f"'{name}' is not in the {location}."
        if similar := _similar_items(name, 0.6):
            msg += f" Did you mean: {', '.join(similar)}?"
        return msg
    have, have_unit = _parse_quantity(item["quantity"])
    used, used_unit = _parse_quantity(quantity)
    if used is None or used <= 0:
        return f"quantity must start with a positive number, e.g. \"2\" or \"1 cup\", got '{quantity}'."
    if have is None or not _same_unit(have_unit, used_unit, stored_name):
        return (
            f"Can't subtract '{quantity}' from '{stored_name}' ({item['quantity']}) "
            "automatically. Work out what's left, then remove_item and add_item it "
            "back with the remaining amount, keeping its details."
        )
    remaining = have - used
    if remaining <= 0:
        _delete_item(location, stored_name)
        return f"Used {quantity} of '{stored_name}' — that's all of it, so it was removed from the {location}."
    item["quantity"] = f"{_format_number(remaining)} {have_unit or used_unit}".strip()
    _upsert_item(location, stored_name, item)
    msg = f"Used {quantity} of '{stored_name}'. {item['quantity']} left in the {location}."
    if _is_low(item):
        msg += f"\n⚠️ '{stored_name}' is running low (threshold {item['low_threshold']})."
    return msg

@mcp.tool()
def update_item(name: str, field: str, value: str, location: str = "fridge") -> dict:
    """Change one field on an item: expiry, location, quantity, name,
    details, made_date, or low_threshold. Use this instead of remove_item +
    add_item. Changing made_date or location does not change expiry: ask
    Kay whether expiry should change too, and suggest a date (made_date + 4
    days for leftovers, or the new location's shelf life when moving).

    name and location find the item the same way remove_item does: name
    matches regardless of case or singular/plural ("egg" finds "eggs"),
    location defaults to "fridge", and if nothing matches, the error
    suggests similar items and where they are.

    value is the new value for field, checked like add_item: name is
    trimmed and lowercased; location is trimmed and lowercased, and a new
    location is created and reported; expiry and made_date must be
    YYYY-MM-DD. An empty value clears details, expiry, made_date, or
    low_threshold; name, location, and quantity can't be cleared. A rename
    or move onto an item that already has that name at that location is
    refused and nothing changes; the two are never merged.

    Returns the full updated item and the field's previous value, or
    {"error": ...} if nothing changed.
    """
    location = _normalize_location(location)
    if error := _check_location(location):
        return {"error": error}
    field = field.strip().lower()
    if field not in UPDATABLE_FIELDS:
        return {"error": f"field must be one of {', '.join(UPDATABLE_FIELDS)}, got '{field}'."}
    name = _normalize_name(name)
    items = _get_items(location).get(location, {})
    stored_name, item = _find_item(items, name)
    if item is None:
        return {"error": _not_found(name, location)}

    if not value.strip():
        if field in REQUIRED_FIELDS:
            return {"error": f"{field} can't be cleared."}
        value = ""
    elif field == "name":
        value = _normalize_name(value)
    elif field == "location":
        value = _normalize_location(value)
        if error := _check_location(value):
            return {"error": error}
    elif field in DATE_FIELDS:
        value = value.strip()
        if not _is_date(value):
            return {"error": f"{field} must be a date written YYYY-MM-DD, got '{value}'."}

    new_location = value if field == "location" else location
    new_name = value if field == "name" else stored_name
    if (new_location, new_name) != (location, stored_name):
        others = _get_items(new_location).get(new_location, {})
        others = {n: i for n, i in others.items() if (new_location, n) != (location, stored_name)}
        clash, _ = _find_item(others, new_name)
        if clash is not None:
            return {"error": (
                f"There's already '{clash}' in the {new_location}, so '{stored_name}' "
                "wasn't changed. Nothing was merged."
            )}

    existing_locations = _get_locations()
    previous = {"name": stored_name, "location": location, **item}[field]
    if not _update_field(location, stored_name, field, value):
        return {"error": (
            f"There's already '{new_name}' in the {new_location}, so '{stored_name}' "
            "wasn't changed. Nothing was merged."
        )}

    updated = _get_items(new_location)[new_location][new_name]
    result = {
        "item": {"name": new_name, "location": new_location, **updated},
        "field": field,
        "previous": previous,
    }
    if new_location not in existing_locations:
        result["created_location"] = (
            f"Created new location '{new_location}'. Existing locations: "
            f"{_format_locations(existing_locations)}. If '{new_location}' was a "
            f"typo, move the item back and call remove_location('{new_location}')."
        )
    return result

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
def get_expiring(days: int = 7, include_expired: bool = True) -> dict:
    """Items expiring within `days` days, plus already-expired ones. Use this
    for 'what's expiring' and the weekly review instead of scanning list_inventory.

    Returns items whose expiry is on or before today + days, soonest first,
    each with name, location, quantity, expiry, and days_left (negative =
    already expired). include_expired=false leaves out items with
    days_left < 0. Items with no expiry aren't listed; no_expiry_count says
    how many there are. unparseable_count is how many have an expiry that
    isn't a YYYY-MM-DD date.
    """
    today = _today()
    expiring = []
    no_expiry_count = 0
    unparseable_count = 0
    for loc, items in _get_items().items():
        for name, item in items.items():
            if not item["expiry"].strip():
                no_expiry_count += 1
                continue
            try:
                expiry = datetime.date.fromisoformat(item["expiry"].strip())
            except ValueError:
                unparseable_count += 1
                continue
            days_left = (expiry - today).days
            if days_left > days or (days_left < 0 and not include_expired):
                continue
            expiring.append({
                "name": name,
                "location": loc,
                "quantity": item["quantity"],
                "expiry": expiry.isoformat(),
                "days_left": days_left,
            })
    expiring.sort(key=lambda i: (i["expiry"], i["name"], i["location"]))
    return {
        "today": today.isoformat(),
        "items": expiring,
        "no_expiry_count": no_expiry_count,
        "unparseable_count": unparseable_count,
    }

# GET /stats: aggregate counts for a public stats page. Counts only, never row
# content. No monthly series: items has no created timestamp, so inventory
# history comes from the showcase repo's daily snapshots instead.

def _count_items_tracked(conn: psycopg.Connection) -> int:
    # Rows currently in items, all locations, expired included.
    return conn.execute("SELECT count(*) FROM items").fetchone()[0]

def _count_locations(conn: psycopg.Connection) -> int:
    # Rows in locations, i.e. every known storage location, empty ones included.
    return conn.execute("SELECT count(*) FROM locations").fetchone()[0]

def _count_expired_now(conn: psycopg.Connection, today: datetime.date) -> int:
    # Items whose expiry is a YYYY-MM-DD date before today (Eastern); blank or malformed expiry doesn't count.
    return conn.execute(
        r"SELECT count(*) FROM items WHERE expiry ~ '^\d{4}-\d{2}-\d{2}$' AND expiry < %s",
        (today.isoformat(),),
    ).fetchone()[0]

async def _count_tools() -> int:
    # MCP tools registered on this server, read from FastMCP's registry.
    return len(await mcp.list_tools())

@mcp.custom_route("/stats", methods=["GET"])
async def stats(request):
    try:
        today = _today()
        with _get_conn() as conn:
            totals = {
                "items_tracked": _count_items_tracked(conn),
                "locations": _count_locations(conn),
                "expired_now": _count_expired_now(conn, today),
            }
        tools = await _count_tools()
    except Exception:
        return PlainTextResponse("stats unavailable", status_code=500)
    return JSONResponse({
        "server": "kitchen-inventory",
        "as_of": today.isoformat(),
        "tools": tools,
        "totals": totals,
        "monthly": [],
    })

def build_app():
    """The ASGI app: FastMCP's streamable-HTTP app plus the user allowlist."""
    app = mcp.streamable_http_app()
    kitchen_auth.install_allowlist(app, AUTH_CONFIG, mcp.settings.streamable_http_path)
    return app

if __name__ == "__main__":
    import uvicorn

    kitchen_auth.install_access_log_filter()
    # Same as mcp.run(transport="streamable-http"), with the allowlist added.
    uvicorn.run(build_app(), host=mcp.settings.host, port=mcp.settings.port,
                log_level=mcp.settings.log_level.lower())
