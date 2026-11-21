# Task 06: get_expiring

Source: Personal AI System project, system/CODING_AGENT_PROMPTS.md #6 and CHANGES.md section 10 (2026-09-27).

You're working on my MCP server "kitchen-inventory", a food inventory (items, storage
location, quantity, expiry, low-stock threshold) used by Claude as a connector.

Add one tool, nothing else:

get_expiring(days=7, include_expired=true)
- Items whose expiry is on or before today + days, sorted by expiry (soonest first).
- Each item: id, name, location, quantity, expiry, days_left (negative = already
  expired).
- include_expired=false leaves out items with days_left < 0.
- Items with no expiry are not listed; return their count as no_expiry_count.
- Use the same timezone handling as the rest of the server.
- Description: "Items expiring within `days` days, plus already-expired ones. Use this
  for 'what's expiring' and the weekly review instead of scanning list_inventory."

Do not build: get_stale, last_updated tracking, or any other new tool.

Constraints:
- No behavior changes to existing tools.
- Update the README's tool list.
- Tests: boundary day (expiry = today + days is included), expired items with and
  without include_expired, items with no expiry excluded and counted.
- When done, give me: a short summary, test results, and the exact deploy steps.
