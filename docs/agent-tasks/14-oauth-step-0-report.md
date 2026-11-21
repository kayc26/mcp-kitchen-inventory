# 14. OAuth step 0: spike report (2026-11-20)

Outcome: **it works end to end, including sign-in inside the Claude iPhone
app, once Google sign-in is replaced by an emailed code (Neon email OTP).**
This changes one decision in ADR-0002 ("Sign-in with Google"). Everything else
in the design held.

## Final results (email-code sign-in, `@neon/auth` pages)

| Path | Result |
|---|---|
| Claude iPhone app: add connector + sign in inside the app | **works** (in-app Safari sheet) |
| Phone app: `whoami`, `list_inventory` | **work**; `whoami` returns the token's `sub` |
| `disable_sign_up` on, unknown email | **blocked**: page says "Code sent" (no account probing), but Neon creates no code, sends no email, creates no user |
| `disable_sign_up` on, existing account | **still signs in** (new session, no new user) |
| 401 + protected-resource metadata | works |
| CIMD | works; Neon registers "Claude" and "Claude Code" clients on its own; DCR off |

Earlier round, with Google sign-in on the first spike branch (since deleted):

| Path | Result |
|---|---|
| claude.ai in desktop Chrome | worked |
| Claude Code (`claude mcp add --transport http`, `/mcp` → Authenticate) | worked |
| Phone app using the desktop sign-in | worked |
| iPhone app signing in itself | **failed**: `{"message":"State integrity check failed"}` on `{issuer}/callback/google` |
| `disable_sign_up` | **did not block** Google sign-up |

## What failed and what fixed it

1. **Hand-written login/consent pages (plain `fetch` to the issuer).** In
   Chrome, Google sign-in at Neon succeeded, but back on `/login` the page saw
   no session. The issuer's cookies are `SameSite=None; Secure; HttpOnly;
   Partitioned`, and the session set on Neon's own callback page isn't visible
   to calls from our page. Neon's fix is a hand-off: Neon redirects back with
   `neon_auth_session_verifier`, and the page trades it for a session.
   **Fix:** use `@neon/auth` (0.8.2, jsDelivr `+esm`) in the pages:
   `createAuthClient(issuer)`, `getSession()` (does the trade),
   `oauth2.continue({selected: true})`, `oauth2.publicClient`, and
   `oauth2.consent({accept})`. The client attaches the signed `oauth_query` to
   every POST and navigates on its own.
2. **iPhone in-app sheet + Google: `State integrity check failed`** at Neon's
   callback, before our pages are involved again. `@neon/auth` didn't change
   it. The root cause wasn't found: Chrome passed the same step, so it's not
   simply the cookie split. A proxy was rejected: unproven to fix it, and
   Neon's guidance says not to re-implement its SDK hand-off.
   **Fix:** sign in with an emailed code (`emailOtp.sendVerificationOtp({email,
   type: 'sign-in'})`, then `signIn.emailOtp({email, otp})`). Every call is a
   `fetch` from our page, with no top-level trip to Neon's domain. Neon's OAuth
   plugin resumes authorization after any sign-in request that carries
   `oauth_query` and sets a session, so the flow goes on to consent.
3. **Closing sign-ups.** `disable_sign_up` (email and password config) didn't
   stop Google sign-ups. With email codes, it does.
4. **SDK host check in tests:** 421 for the test client's default host. Fixed
   with `base_url="http://localhost:8000"` (FastMCP's DNS-rebinding protection
   applies to host `127.0.0.1`; the deploy uses `0.0.0.0`).
5. **Access log printed full queries** (signed `oauth_query`, one-time
   verifier). **Fix:** a filter on `uvicorn.access` that drops query strings.

## Settings that worked

- Neon OAuth server on a **child branch** (the Neon MCP tool's "root branch
  only" note is wrong). `login_page` = `<render>/login`, `consent_page` =
  `<render>/consent`, `mcp.resource` = `<render>/mcp`, trusted domain = Render
  origin. CIMD on by default, DCR off.
- Email codes come from Neon's shared sender `auth@mail.myneon.app` (check
  spam). Neon recommends your own SMTP for production.
- Issuer metadata is served only with the well-known path *appended*
  (`<issuer>/.well-known/oauth-authorization-server`); RFC 8414 path-inserted
  forms 404. Claude and Claude Code got through anyway.
- Verifier: PyJWT, EdDSA, `iss` exact (the full issuer URL with path), `aud`
  contains the resource (a list), `exp`/`sub` required. The SDK's
  `validate_token_resource=False`, since the verifier checks `aud`. mcp ≥ 1.17.

## For the build prompt

- ADR-0002: replace "Sign-in with Google" with email-code sign-in (Neon email
  OTP); drop the Google provider.
- Closing sign-ups works: `disable_sign_up` blocks new emails, and existing
  users still sign in. Keep the user-id allowlist as a second gate in the
  server.
- Fail closed: the spike runs open when `AUTH_*` env vars are missing.
- Keep the access-log query filter.
- Access tokens carry no email; `whoami` returned `sub` only, as asked.
- `list_items` doesn't exist; the tool is `list_inventory`.
- jsDelivr's `+esm` leaves sub-dependency versions unpinned (it resolved
  better-auth 1.7.14); consider bundling a fixed copy.
- Claude Code and claude.ai desktop weren't re-tested with email codes (they
  were tested with Google); the email-code flow doesn't depend on the browser
  in the same way, but re-test in the build.
