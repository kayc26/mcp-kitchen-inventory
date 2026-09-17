"""your-first-instrument — a sense of time for a model that has none.

Why time? Ask your Claude "how long have we been talking?" WITHOUT this
connected. It can only guess: no clock lives in a context window. This
server is the smallest honest fix — and the pattern generalizes to any
instrument you can imagine. See docs/adr/ for every choice made here.
"""
import json
import os
from datetime import datetime, timezone
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "your-first-instrument",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", 8000)),
)

# Fridge inventory storage. Plain JSON file, no database needed for this
# scale. Note: on Render's free tier the disk is ephemeral, so this file
# will NOT persist across restarts/deploys — accepted limitation, not a bug.
FRIDGE_FILE = os.path.join(os.path.dirname(__file__), "fridge.json")

def _load_fridge() -> dict:
    if not os.path.exists(FRIDGE_FILE):
        return {}
    with open(FRIDGE_FILE) as f:
        return json.load(f)

def _save_fridge(fridge: dict) -> None:
    with open(FRIDGE_FILE, "w") as f:
        json.dump(fridge, f, indent=2)

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
def add_item(name: str, quantity: str = "1", expiry: str = "") -> str:
    """Add an item to the fridge inventory (overwrites if it already exists).

    expiry is optional, format YYYY-MM-DD.
    """
    fridge = _load_fridge()
    fridge[name] = {"quantity": quantity, "expiry": expiry}
    _save_fridge(fridge)
    return f"Added '{name}'. Current inventory: {fridge}"

@mcp.tool()
def remove_item(name: str) -> str:
    """Remove an item from the fridge inventory (used up or thrown out)."""
    fridge = _load_fridge()
    if name not in fridge:
        return f"'{name}' is not in the inventory."
    del fridge[name]
    _save_fridge(fridge)
    return f"Removed '{name}'. Current inventory: {fridge}"

@mcp.tool()
def list_inventory() -> str:
    """The full current fridge inventory."""
    fridge = _load_fridge()
    if not fridge:
        return "The fridge is empty."
    return str(fridge)

@mcp.tool()
def my_tool() -> str:
    """YOURS. Rename it, give it a real purpose, make the model reach
    something it couldn't before. (Track ideas: docs/TRACKS.md)"""
    return "Not built yet — that's the point. Edit server.py."

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
