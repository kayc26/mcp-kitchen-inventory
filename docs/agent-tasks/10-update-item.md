# 10. kitchen-inventory: update_item (decided 2026-10-02)

You're working on my MCP server "kitchen-inventory", a food inventory (items, storage
location, quantity, expiry, low-stock threshold) used by Claude as a connector.

Problem: there's no way to change an item. Changing an expiry, moving an item to the
freezer, or fixing a quantity means remove_item then add_item. That isn't atomic (if
the add fails, the item is gone) and it loses fields set at intake.

Add one tool, nothing else:

update_item(name, location, field, value)
- Finds the item the same way remove_item does (name matches regardless of case or
  singular/plural; location defaults to "fridge"; if nothing matches, suggest similar
  items and where they are).
- One field per call. field is one of: name, details, location, quantity, expiry,
  made_date, low_threshold.
- Validation matches add_item. Dates are YYYY-MM-DD; reject anything else. name is
  trimmed and lowercased. A new location is created and reported, like add_item.
- Empty value clears details, expiry, made_date, or low_threshold. name, location,
  and quantity can't be cleared.
- If a rename or move would collide with an existing item of the same name at that
  location, change nothing and say so. Don't merge.
- Changing made_date or location never changes expiry.
- Returns the full updated item (every field) and the previous value of the field.
- Description: "Change one field on an item: expiry, location, quantity, name,
  details, made_date, or low_threshold. Use this instead of remove_item + add_item.
  Changing made_date or location does not change expiry: ask Kay whether expiry
  should change too, and suggest a date (made_date + 4 days for leftovers, or the new
  location's shelf life when moving)."

Also edit two existing descriptions (wording only, no behavior change):
- use_item: where it says to remove_item and add_item back when units don't match,
  say to use update_item field quantity with the remaining amount instead.
- add_item: where it says "move the item" after a location typo, say "move the item
  with update_item".

Do not build: bulk updates, edit history, last_updated tracking, an opened_date
field, auto-recomputing expiry, an id-based lookup, or anything else new.

Constraints:
- No other behavior changes. Existing data must keep working.
- Update the README's tool list.
- Tests: each field updates; invalid date rejected; empty value clears optional
  fields; clearing name, location, or quantity rejected; rename or move into an
  existing item rejected with nothing changed; move to a new location creates it;
  made_date change leaves expiry untouched; no match returns suggestions; result
  includes the previous value.
- When done, give me: a short summary, test results, and the exact deploy steps.