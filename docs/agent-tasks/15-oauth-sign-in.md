# 15. kitchen-inventory: sign-in with an emailed code (decided 2026-11-20, see ADR-0002)

Save in the repo as docs/agent-tasks/15-oauth-sign-in.md. Built from the step 0 report
(docs/agent-tasks/14-oauth-step-0-report.md). Per-person
recipes and profiles (ADR-0003) are later prompts.

```
You're working on my MCP server "kitchen-inventory", a food inventory (items, storage
location, quantity, expiry, low-stock threshold) used by Claude as a connector. It is
a Python FastMCP server on Render, with its database in Neon. Today it has no auth:
anyone with the URL can read and write.

Goal: the live server requires a signed-in, allowed user on every MCP request.
Sign-in is OAuth with Neon Auth as the issuer and an emailed 6-digit code (Neon email
OTP). Step 0 (task 14) proved this works from the Claude iPhone app; read its report
first: docs/agent-tasks/14-oauth-step-0-report.md on the auth-spike branch. Reuse
what it learned, but write the code fresh on a new branch "oauth" from main. Do not
merge auth-spike.

The inventory stays shared: every allowed user sees and changes the same items. No
tool gains a user argument, and no table changes in this task.

Do these things, nothing else:

1. Token verification (SDK, no custom middleware): mcp[cli]>=1.17,<2 and
   pyjwt[crypto]. FastMCP with a token verifier and AuthSettings, so /mcp returns
   401 with protected-resource metadata when there's no valid token. The verifier
   checks: EdDSA signature against the issuer's JWKS (cached, refetched on an
   unknown kid), iss exactly equal to AUTH_ISSUER, aud containing AUTH_RESOURCE
   (aud may be a list), exp, and a non-empty sub. validate_token_resource=False
   only because the verifier checks aud itself; say so in a comment.

2. Allowlist: env var ALLOWED_USER_IDS, a comma-separated list of Neon user ids
   (sub). A valid token whose sub isn't listed gets "not allowed" (403 or the SDK's
   equivalent) and is logged with its sub only, so I can find ids to add. Every
   MCP request is checked; there is no read/write split on this server.

3. Fail closed: at startup, if AUTH_ISSUER, AUTH_RESOURCE, AUTH_JWKS_URL or
   ALLOWED_USER_IDS is missing or empty, refuse to start with a clear error. No
   bypass flag. Tests use a locally generated key and a fake JWKS.

4. /login and /consent pages, served by the server, using Neon's browser client
   @neon/auth (the spike used 0.8.2):
   - Serve a fixed, vendored copy from this repo (a built ESM file with exact
     pinned versions, including better-auth), not a CDN URL that resolves
     sub-dependencies at load time. Say how to rebuild it.
   - /login: email field -> emailOtp.sendVerificationOtp({email, type:
     'sign-in'}), then a code field -> signIn.emailOtp({email, otp}). After "send
     code", always show "If this email can sign in, a code was sent", whether or
     not the account exists. If already signed in, continue the flow
     (getSession, then oauth2.continue({selected: true})).
   - /consent: show the client name and redirect hosts from oauth2.publicClient,
     warn if every redirect is loopback, then oauth2.consent({accept}).
   - Both: Content-Security-Policy with frame-ancestors 'none', plain HTML, no
     styling work, no Google button.

5. Keep the access-log filter that drops query strings (the signed oauth_query and
   the one-time verifier must never be logged). Never log tokens, codes or emails.

6. Leave GET /stats unauthenticated and unchanged. Remove nothing else. Do not add
   the spike's whoami tool or /spike-log.

7. render.yaml: build command with the new dependencies; env var keys AUTH_ISSUER,
   AUTH_RESOURCE, AUTH_JWKS_URL, ALLOWED_USER_IDS (sync: false). README: how sign-in
   works, the env vars, and how to add a user.

Rollout, live steps, one at a time. Before each, tell me exactly what will change
and wait for my go:
a. Neon, kitchen's production branch: enable Neon Auth with email OTP only (no
   Google, no passwords) and Neon's shared email sender. Enable the OAuth server
   with login_page <live Render URL>/login, consent_page <live Render URL>/consent,
   mcp.resource <live Render URL>/mcp, the Render origin as a trusted domain, CIMD
   on, DCR off. Report what this adds to the production database (the neon_auth
   schema) before doing it.
b. Render, the live kitchen-inventory service: set the four env vars, with
   ALLOWED_USER_IDS empty for now. The server will refuse to start until it's set,
   so do b and c together.
c. Merge oauth to main and deploy. From here my current connector stops working
   until I add it again and sign in.
d. I add the connector in the Claude iPhone app and sign in. Expect "not allowed".
   You find my user id in Neon, I add it to ALLOWED_USER_IDS, and Render restarts.
e. Test: Claude iPhone app, claude.ai in desktop Chrome, and Claude Code
   (claude mcp add --transport http, then /mcp -> Authenticate) each sign in and
   call list_inventory. A second email that isn't on the allowlist gets "not
   allowed".
f. Give me the steps, not done now, for when my roommates join: they sign in, I
   add their ids, then I turn on disable_sign_up in Neon (step 0 confirmed it
   blocks new emails while existing users still sign in).

Do not build: Google sign-in, passwords, a custom SMTP sender, per-user columns or
any migration of existing tables, per-person recipes or profiles, a households
table, roles or admin users, a read/write split, a user argument on any tool,
changes to /stats, the stats GitHub Action, the Claude Code plugin or .mcp.json,
changes to workout-log or food-journal, a same-origin auth proxy, styling of the
pages, or anything else new.

Constraints:
- Never run tests or experiments against the production Neon branch. Use a test
  branch for anything that needs a database.
- Never paste secrets or the live URLs into committed files; use env vars.
- Existing tools and data must keep working for allowed users.
- Tests: no token -> 401 with the resource_metadata header; wrong issuer, wrong
  audience (single and list), expired, missing sub, bad signature, unknown kid ->
  rejected; valid token, sub not allowed -> not allowed; valid allowed token ->
  list_inventory works; startup refuses with each required env var missing; access
  log drops query strings; /stats still answers without a token; login and consent
  pages are served with frame-ancestors 'none'. Every existing test passes.
- Commit this task file as docs/agent-tasks/15-oauth-sign-in.md on oauth.
- When done, give me: a short summary of changes, full test results, each live step
  with what happened, every env var and setting and where it lives, and the
  roommate steps from f.
```
