# 10. kitchen-inventory: GET /stats

Save in the repo as docs/agent-tasks/10-stats-endpoint.md.

```
You're working on my MCP server "kitchen-inventory", used by Claude as a connector.

Add one read-only HTTP endpoint, GET /stats, that returns aggregate counts for a
public stats page. Counts only: no names, notes, places, items, or any row content.

Step 0, before any change, tell me:
- Which tables have a created timestamp, and which don't.
- Anything that would make a monthly series misleading (e.g. bulk imports that all
  share one created date, rows with null dates). Stop and ask if it's unclear.

Response (JSON):
{
  "server": "kitchen-inventory",
  "as_of": "YYYY-MM-DD",            // today, in the server's timezone
  "tools": <int>,                   // number of MCP tools registered, read from the
                                    // server's own registry, not hardcoded
  "totals": { ... },                // see below
  "monthly": [ {"month": "YYYY-MM", ...}, ... ]   // oldest first; see below
}

Rules:
- No auth, GET only, no CORS headers (it's called server to server).
- Every count is defined in code, in one function per count, with a one-line
  comment stating the definition. Same definition every day.
- monthly: one row per calendar month from the first month with data to the
  current month, with zeros for empty months. Counts are events in that month,
  not cumulative.
- Rows with no usable date are left out of monthly and counted in
  totals.undated_<name>.
- Errors return 500 with a short message, never partial data.

Counts for this server:
totals:
- items_tracked: rows currently in the inventory, all locations, expired included.
- locations: number of storage locations.
- expired_now: items whose expiry is before today.
monthly:
- items_added: items by created date, if a created timestamp exists. If none
  exists, add a nullable created_at going forward (existing rows stay null and
  count as undated) and tell me in step 0.

Do not build: auth, caching, a dashboard, per-day series, cumulative series
(the page computes those), new MCP tools, or anything else new.

Constraints:
- No changes to existing tools or data.
- Update the README with the endpoint and each count's definition.
- Tests: response shape; each count on a small fixture; empty months return
  zeros; undated rows are excluded from monthly and counted; tools matches the
  registry.
- When done, give me: the step 0 answers, a short summary, test results, a curl
  example against the deployed URL, and the exact deploy steps.
```
