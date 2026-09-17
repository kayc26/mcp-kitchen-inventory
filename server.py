"""your-first-instrument — a sense of time for a model that has none.

Why time? Ask your Claude "how long have we been talking?" WITHOUT this
connected. It can only guess: no clock lives in a context window. This
server is the smallest honest fix — and the pattern generalizes to any
instrument you can imagine. See docs/adr/ for every choice made here.
"""
import json
import os
import re
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "your-first-instrument",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

# Kitchen inventory storage: two sections, fridge and freezer. Plain JSON
# file, no database needed for this scale. Note: on Render's free tier the
# disk is ephemeral, so this file will NOT persist across restarts/deploys
# — accepted limitation, not a bug.
STORAGE_FILE = os.path.join(os.path.dirname(__file__), "kitchen.json")
LOCATIONS = ("fridge", "freezer")

def _load_kitchen() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {loc: {} for loc in LOCATIONS}
    with open(STORAGE_FILE) as f:
        data = json.load(f)
    for loc in LOCATIONS:
        data.setdefault(loc, {})
    return data

def _save_kitchen(data: dict) -> None:
    with open(STORAGE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def _check_location(location: str) -> str | None:
    """None if valid, else an error message."""
    if location not in LOCATIONS:
        return f"location must be one of {LOCATIONS}, got '{location}'."
    return None

def _leading_number(s: str) -> float | None:
    """Pull the leading number out of a quantity string like '2 eggs' -> 2.0."""
    match = re.match(r"\s*(\d+(\.\d+)?)", s)
    return float(match.group(1)) if match else None

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
    """Add an item to the fridge or freezer (overwrites if it already exists).

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
    data = _load_kitchen()
    data[location][name] = {
        "quantity": quantity,
        "expiry": expiry,
        "made_date": made_date,
        "low_threshold": low_threshold,
    }
    _save_kitchen(data)
    msg = f"Added '{name}' to the {location}. Current {location}: {data[location]}"
    if _is_low(data[location][name]):
        msg += f"\n⚠️ '{name}' is already at or below its low-stock threshold."
    return msg

@mcp.tool()
def remove_item(name: str, location: str = "fridge") -> str:
    """Remove an item from the fridge or freezer (used up or thrown out).

    location is "fridge" or "freezer".
    """
    if error := _check_location(location):
        return error
    data = _load_kitchen()
    if name not in data[location]:
        return f"'{name}' is not in the {location}."
    del data[location][name]
    _save_kitchen(data)
    return f"Removed '{name}' from the {location}. Current {location}: {data[location]}"

@mcp.tool()
def list_inventory(location: str = "all") -> str:
    """The current inventory, flagging anything running low.

    location is "fridge", "freezer", or "all" (default) for both.
    """
    if location != "all" and (error := _check_location(location)):
        return error
    data = _load_kitchen()
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
    data = _load_kitchen()
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
