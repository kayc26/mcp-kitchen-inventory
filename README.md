# kitchen-inventory — the CAM MCP starter

*A rig for your experiments, not a finished thing. Cloned in Session 3 of
Computationally Assisted Metacognition (CIS 7000, Penn, Fall 2026).*

The premise, in one breath: **an AI model only knows what is in its context
window — and an MCP server is how you hand it an instrument** so it can reach
what it lacks (a clock, a dataset, your notes, a museum's collection).
Building one is not hard. That is the whole lesson. By the end of the hour
yours will be running, connected to Claude, and answering questions the bare
model cannot.

## What's in the rig

- `server.py` — a small, working MCP server: a food inventory
  tracker (fridge, freezer, pantry, or any location you name) backed by
  Postgres. Eight tools: `list_inventory`, `add_item`, `use_item`, `update_item`,
  `remove_item`, `remove_location`, `check_low_stock`, `get_expiring`. Leftovers added with
  a `made_date` and no `expiry` get `expiry = made_date + 4 days`.
- `docs/adr/` — **the choices live here, not in the code.** Every decision
  this repo made for you is written down with its reasons, so you inherit
  understanding, not just files. Disagree with one? Write the next ADR.
- `docs/TRACKS.md` — three feasible directions, sized for one session.
- `.vscode/` — the environment configures itself (extensions, project color).
- `render.yaml` — one-click deploy to Render's free tier, so your instrument
  gets a URL anyone's Claude can connect to.

## What you might need to install (once per machine)

macOS ships less than you'd think. In Terminal, in order (skip what you have):

```bash
# 1. Homebrew — the missing package manager for macOS (from brew.sh):
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# 2. uv — the Python project runner (installs Python itself if needed):
brew install uv
# 3. cloudflared — the tunnel that lets claude.ai reach your laptop:
brew install cloudflared
# 4. git — Apple installs it on first use; this just triggers that:
git --version
```

Windows: Do all of this inside WSL (see the course setup guide); the same
commands work there with `apt`-flavored Homebrew or the uv curl installer.

## Run it (2 minutes)

```bash
uv run server.py     # THAT'S IT — environment built, deps resolved, server up (port 8000)
```

Why not `pip install`? Your machine will refuse, and it's right to — see
`docs/adr/0004-virtual-environments-and-uv.md`: every project gets its own room.

Then hand it to your **local** collaborator (the claude CLI reaches localhost;
the browser claude.ai can't — it calls from the cloud, which is exactly why
deployment exists):

```bash
claude mcp add --transport http kitchen-inventory http://localhost:8000/mcp
```

Ask it: *"What's in my fridge? Add 2 eggs, expiry 2026-10-01."* The model
still can't see inside your fridge; it learned to consult one. Yours.

**To reach the browser claude.ai, a tunnel is REQUIRED** — claude.ai calls
from Anthropic's cloud and can never see your laptop directly:

```bash
brew install cloudflared          # once
cloudflared tunnel --url http://localhost:8000   # prints your public URL
```

Add that URL (`https://…trycloudflare.com/mcp`) in claude.ai → Settings →
Connectors — both surfaces now hold your instrument. The tunnel dies with
your terminal; when you want a PERMANENT home, deploy to Render (ADR-0003).

## Deploy it — a permanent home (Render, free)

The tunnel dies with your terminal. For an instrument that outlives your laptop:

1. **render.com** → Sign in **with GitHub** (the account holding your repo).
2. **New +** → **Blueprint** → select your repo → **Apply**. (`render.yaml` does the rest.)
3. ~2 minutes of build → copy your `https://….onrender.com` URL.
4. claude.ai → Settings → Connectors → edit `kitchen-inventory` → swap URL to the Render one + `/mcp`.

Free-tier truth (ADR-0003): the instance naps when idle — first call after a nap
takes ~30s. Fine for an instrument; now you know why.

## Sign-in

Every MCP request needs a signed-in, allowed user (ADR-0002 in
personal-ai-system). Sign-in is OAuth, with Neon Auth as the issuer and a
6-digit code sent by email. Neon sends it from its shared address,
`auth@mail.myneon.app`, so check spam.

How it works:

1. Claude calls `/mcp` without a token and gets `401` pointing to
   `/.well-known/oauth-protected-resource/mcp`, which names the Neon issuer.
2. Claude identifies itself to the issuer with a Client ID Metadata Document
   (no setup) and sends you to this server's `/login` page.
3. You enter your email, then the code, then approve Claude on `/consent`.
   The pages use Neon's browser client, served from
   `static/neon-auth-client.js` (rebuild: `auth-client/README.md`).
4. Claude calls `/mcp` with the access token. The server checks its signature
   (issuer's JWKS), issuer, audience and expiry (`kitchen_auth.py`), then
   checks that its user id is in `ALLOWED_USER_IDS`. If it isn't, `403`.

Everyone allowed shares the same inventory. Tools take no user argument.
`GET /stats` stays public.

Env vars (Render → the service → Environment). The server **refuses to
start** if any is missing or empty:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon connection string |
| `AUTH_ISSUER` | the Neon branch's Auth URL, exactly, e.g. `https://ep-….neonauth.….neon.tech/neondb/auth` |
| `AUTH_RESOURCE` | this server's MCP URL: `https://<service>.onrender.com/mcp` |
| `AUTH_JWKS_URL` | `<AUTH_ISSUER>/.well-known/jwks.json` |
| `ALLOWED_USER_IDS` | comma-separated Neon user ids, e.g. `id1,id2` |

Neon settings, on the same branch: Neon Auth with email OTP sign-in, the
OAuth server enabled with login page `<Render URL>/login`, consent page
`<Render URL>/consent`, MCP resource `<Render URL>/mcp`, CIMD on, DCR off,
and the Render URL as a trusted domain.

### Add a user

1. They add the connector (`<Render URL>/mcp`) in Claude and sign in with
   their email. Claude reports the server as not allowed (`403`). That's
   expected.
2. Find their user id: Render logs show `{"auth": "not_allowed", "sub": "<id>"}`,
   or look them up in Neon's SQL Editor on the same branch:
   `SELECT id, email FROM neon_auth."user"`.
3. Append the id to `ALLOWED_USER_IDS` in Render (comma-separated) and save.
   Render restarts the service.
4. They reconnect in Claude.

Once everyone has signed up, close sign-ups in Neon:
`neon neon-auth config email-password update --disable-sign-up` (Neon CLI,
on the server's branch). New emails then get no code, while existing users
still sign in.

Running locally needs the same four auth variables, pointed at a Neon branch
with Neon Auth and the OAuth server set up for your local or tunnel URL.

## Stats endpoint

`GET /stats` returns aggregate counts for a public stats page: counts only,
never names, notes, places, or any row content. No sign-in needed, GET only, no CORS
(it's called server to server). Errors return 500 with a short message,
never partial data.

```json
{
  "server": "kitchen-inventory",
  "as_of": "2026-10-05",
  "tools": 8,
  "totals": {"items_tracked": 95, "locations": 4, "expired_now": 3},
  "monthly": []
}
```

- `as_of` — today in America/New_York, the server's timezone.
- `tools` — number of MCP tools registered, read from the server's registry.
- `totals.items_tracked` — rows currently in the inventory, all locations, expired included.
- `totals.locations` — every known storage location, empty ones included.
- `totals.expired_now` — items whose expiry is a YYYY-MM-DD date before
  `as_of`; items with no expiry don't count.
- `monthly` — always `[]`. Items have no created timestamp, so this server
  keeps no history; inventory history comes from daily snapshots taken by the
  showcase repo, not from this server.

## License

MPL-2.0 with a Template Output Grant — the template stays open with attribution; what YOU build from it may be Apache-2.0 (open, attributed) or fully closed. See LICENSE + TEMPLATE-GRANT.md.
