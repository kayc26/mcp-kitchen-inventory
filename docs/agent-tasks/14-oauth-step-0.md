# 14. kitchen-inventory: OAuth step 0, prove sign-in end to end (decided 2026-11-20, see ADR-0002)

Save in the repo as docs/agent-tasks/14-oauth-step-0.md. This is a spike: it answers
questions and proves one path. It does not turn auth on for the live server. The build
prompt comes after, written from this prompt's report.

```
You're working on my MCP server "kitchen-inventory", a food inventory (items, storage
location, quantity, expiry, low-stock threshold) used by Claude as a connector. It is
a Python FastMCP server (server.py, streamable-http transport, mcp[cli]>=1.2,<2),
deployed on Render, with its database in Neon.

Goal: find out, before building anything for real, whether OAuth through Neon Auth
works for this server, from the Claude app on my phone. The server is open today
(no auth). An earlier attempt at Neon Auth (Neon project "personal-ai-auth") failed
for a reason nobody wrote down, so this spike is the test. If it hits a wall, stop
and report the wall; don't work around it with a different design.

The design is already decided (don't revisit it, report if it can't work):
- Neon Auth (Managed Better Auth) is the OAuth issuer, with its OAuth provider plugin
  for MCP servers and Client ID Metadata Documents (CIMD).
- Sign-in with Google.
- The server returns 401 with protected-resource metadata when there's no token. It
  verifies each access token: signature (JWKS), iss exact, aud contains the server's
  URL. It maps sub to a user. No tool ever takes a user argument.
- The server serves its own /login and /consent pages; its domain is a trusted
  domain on the issuer.
- Later (not now): an allowlist of user ids in an env var, plus sign-ups closed in
  Neon.

Keep the live server untouched:
- Work on a git branch "auth-spike". Do not merge it to main.
- Use a new Neon branch "auth-spike" of kitchen's existing Neon project (the one
  holding the neondb database). Enable Neon Auth on that branch only. Do not change
  auth settings on the production branch, and do not use "personal-ai-auth".
- Deploy to a new, separate Render web service (free plan) from the auth-spike
  branch, pointed at the Neon auth-spike branch. The existing kitchen-inventory
  Render service, its env vars, and my existing Claude connector stay as they are.
- Anything that needs my account (creating the Render service, a Google OAuth
  client, adding the connector in the Claude app): tell me exactly what to click
  and wait. Never paste secrets into files; use env vars.

Step A, answer from docs and code before changing anything:
1. Python mcp SDK: which version first lets FastMCP serve protected-resource
   metadata and plug in a token verifier (or the current equivalent)? What would
   change in pyproject.toml and render.yaml? If the SDK can't do it, describe the
   small middleware that would do the 401 and the verification instead.
2. Neon Auth on a branch: one issuer per branch? What are the issuer URL, JWKS URL,
   and how is mcp.resource set? Is CIMD on by default?
3. Google sign-in in Neon Auth: shared development credentials, or must I create my
   own Google OAuth client? Any limits on the shared ones (consent screen name,
   user cap, production use)?
4. Can sign-ups be closed in Neon Auth once people have registered? How?
5. What do the /login and /consent pages need to do (which Neon Auth calls), and
   are there cross-site cookie issues with the pages on the Render domain and the
   issuer on Neon's domain?
Report these answers and wait for my go-ahead before Step B.

Step B, prove it end to end on the spike deploy:
1. Raise the mcp version only if Step A says so.
2. Add token verification as Step A found (SDK hooks or middleware). Requests
   without a valid token get 401 with protected-resource metadata. Read the issuer,
   resource URL, and JWKS URL from env vars.
3. Add minimal /login (Sign in with Google) and /consent (approve) pages. Plain
   HTML, no styling work.
4. Add one temporary tool, whoami, that returns the verified user id and email
   from the token. Existing tools stay as they are.
5. I add the spike URL as a custom connector in the Claude app on my phone, sign in
   with Google, and call whoami and one existing tool (list_items or
   get_expiring). Also check that Claude Code can connect with the same flow.
6. Write down each step that failed and what fixed it, so the build prompt can
   avoid it.

Do not build: the user allowlist, closing sign-ups, user_id columns or any
migration, per-user scoping of queries, profiles, changes to workout-log or
food-journal, styling of the pages, changes to the live Render service or the
production Neon branch, merging auth-spike to main, or anything else new.

Constraints:
- Existing tests must pass on the auth-spike branch. Add tests for: no token -> 401
  with the metadata header; wrong issuer, wrong audience, expired token, and bad
  signature -> rejected; valid token -> whoami returns its sub. Use a locally
  generated key and a fake JWKS; never real tokens in fixtures.
- Commit this task file as docs/agent-tasks/14-oauth-step-0.md on auth-spike.
- When done, give me: the Step A answers with links to the docs you used, what
  worked and what failed in Step B (including whether sign-in from the phone
  completed), test results, every env var and setting you added and where, and how
  to tear the spike down (Render service, Neon branch).
```
