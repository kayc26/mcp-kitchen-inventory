"""Sign-in for kitchen-inventory (task 15, ADR-0002 in personal-ai-system).

Every MCP request needs an OAuth access token from Neon Auth (Managed Better
Auth with its OAuth provider plugin). People sign in with a 6-digit code sent
by email, on the /login and /consent pages served below. A valid token isn't
enough: its user id (`sub`) must also be in ALLOWED_USER_IDS.

Required env vars (the server refuses to start without them, see load_config):
  AUTH_ISSUER       the Neon branch's Auth URL, exact, e.g. https://ep-….neonauth…/neondb/auth
  AUTH_RESOURCE     this server's MCP URL, e.g. https://<service>.onrender.com/mcp
  AUTH_JWKS_URL     <AUTH_ISSUER>/.well-known/jwks.json
  ALLOWED_USER_IDS  comma-separated Neon user ids allowed to use the server

Logs carry rejection reasons and, for "not allowed", the user id only. Never
tokens, codes, emails or query strings.
"""
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import jwt
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

REQUIRED_ENV = ("AUTH_ISSUER", "AUTH_RESOURCE", "AUTH_JWKS_URL", "ALLOWED_USER_IDS")

STATIC_DIR = Path(__file__).parent / "static"
CLIENT_JS = "neon-auth-client.js"  # built by auth-client/ (see auth-client/README.md)


def _log(event: str, **fields: Any) -> None:
    print(json.dumps({"auth": event, **fields}), flush=True)


# --- Config: fail closed ------------------------------------------------------

@dataclass(frozen=True)
class AuthConfig:
    issuer: str
    resource: str
    jwks_url: str
    allowed_user_ids: frozenset[str]


def load_config(environ=os.environ) -> AuthConfig:
    """Read the auth env vars. Exits the process if any is missing or empty.

    There's deliberately no way to run without auth.
    """
    values = {k: (environ.get(k) or "").strip() for k in REQUIRED_ENV}
    allowed = frozenset(u.strip() for u in values["ALLOWED_USER_IDS"].split(",") if u.strip())
    missing = [k for k in REQUIRED_ENV if not values[k]]
    if not allowed and "ALLOWED_USER_IDS" not in missing:
        missing.append("ALLOWED_USER_IDS")
    if missing:
        sys.exit(
            "kitchen-inventory refuses to start: missing or empty env var(s): "
            + ", ".join(missing)
            + ". Every MCP request requires sign-in; see README.md (Sign-in)."
        )
    return AuthConfig(values["AUTH_ISSUER"], values["AUTH_RESOURCE"], values["AUTH_JWKS_URL"], allowed)


# --- Token verification (plugged into FastMCP) --------------------------------

class NeonTokenVerifier:
    """Accepts a Neon Auth access token only if all of these hold:
    EdDSA signature from the issuer's JWKS, `iss` exactly AUTH_ISSUER, `aud`
    containing AUTH_RESOURCE (a string or a list), not expired, non-empty `sub`.
    """

    def __init__(self, issuer: str, resource: str, jwks_url: str, jwk_client: Any = None):
        self.issuer = issuer
        self.resource = resource
        # PyJWKClient caches the keys and refetches the JWKS when a token names
        # an unknown kid (key rotation).
        self.jwk_client = jwk_client or jwt.PyJWKClient(jwks_url, cache_keys=True)

    def _decode(self, token: str) -> dict:
        key = self.jwk_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["EdDSA"], issuer=self.issuer, audience=self.resource,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        if not isinstance(claims.get("sub"), str) or not claims["sub"].strip():
            raise jwt.InvalidTokenError("empty sub")
        return claims

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except Exception as e:  # any failure means "not a valid token"
            _log("token_rejected", reason=type(e).__name__)
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id", "")),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=claims["exp"],
            resource=self.resource,
            subject=claims["sub"],
            claims={"iss": claims["iss"]},
        )


def fastmcp_auth_kwargs(config: AuthConfig) -> dict:
    """FastMCP(...) kwargs: /mcp answers 401 + protected-resource metadata
    without a valid token, and serves that metadata."""
    return {
        "token_verifier": NeonTokenVerifier(config.issuer, config.resource, config.jwks_url),
        # False only because NeonTokenVerifier checks `aud` itself; the SDK's
        # own resource check would be a second, redundant check.
        "auth": AuthSettings(issuer_url=config.issuer, resource_server_url=config.resource,
                             validate_token_resource=False),
    }


# --- Allowlist ----------------------------------------------------------------

class AllowlistMiddleware:
    """403 for a valid token whose user isn't in ALLOWED_USER_IDS.

    Must run after the SDK's AuthenticationMiddleware (it reads scope["user"]),
    so install_allowlist appends it as the innermost middleware. Requests
    without a valid token pass through to the SDK, which answers 401.
    """

    def __init__(self, app, allowed_user_ids: frozenset[str], mcp_path: str):
        self.app = app
        self.allowed = allowed_user_ids
        self.mcp_path = mcp_path.rstrip("/")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (
            scope["path"] == self.mcp_path or scope["path"].startswith(self.mcp_path + "/")
        ):
            user = scope.get("user")
            if isinstance(user, AuthenticatedUser) and user.access_token.subject not in self.allowed:
                _log("not_allowed", sub=user.access_token.subject)
                response = JSONResponse(
                    {"error": "forbidden",
                     "error_description": "This account is not allowed on this server."},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def install_allowlist(app, config: AuthConfig, mcp_path: str) -> None:
    from starlette.middleware import Middleware
    if app.middleware_stack is not None:
        raise RuntimeError("install_allowlist must run before the app handles a request")
    app.user_middleware.append(Middleware(AllowlistMiddleware, allowed_user_ids=config.allowed_user_ids,
                                          mcp_path=mcp_path))


# --- Access log: never log query strings --------------------------------------

class DropQueryString(logging.Filter):
    """uvicorn's access log shows the path only. Queries on /login and /consent
    carry the signed oauth_query and the one-time neon_auth_session_verifier."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            args[2] = str(args[2]).split("?", 1)[0]
            record.args = tuple(args)
        return True


def install_access_log_filter() -> None:
    logging.getLogger("uvicorn.access").addFilter(DropQueryString())


# --- /login and /consent ------------------------------------------------------

_HEADERS = {"Content-Security-Policy": "frame-ancestors 'none'", "Cache-Control": "no-store"}

_COMMON_JS = """
import { createAuthClient } from "/static/%(client_js)s";
const auth = createAuthClient(%(issuer)s);
const params = new URLSearchParams(location.search);
const out = (m) => { document.getElementById('msg').textContent = m; };
const errText = (error) => error.message || error.statusText || ('HTTP ' + error.status);
"""

_LOGIN_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Sign in: kitchen-inventory</title><h1>Sign in to kitchen-inventory</h1>
<form id="send"><label>Email <input id="email" type="email" autocomplete="email" required></label>
<button>Send code</button></form>
<form id="verify" hidden><label>Code <input id="otp" inputmode="numeric" autocomplete="one-time-code" required></label>
<button>Sign in</button></form><p id="msg"></p>
<script type="module">%(common)s
// Every call is a fetch from this page, so all of Neon's cookies stay with it.
const isOAuth = params.has('client_id') && params.has('response_type');
async function resume() {
  out('Signed in, continuing…');
  const { error } = await auth.oauth2.continue({selected: true});  // navigates on success
  if (error) out('Could not continue: ' + errText(error) + '. Go back to Claude and try again.');
}
document.getElementById('send').onsubmit = async (e) => {
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  const { error } = await auth.emailOtp.sendVerificationOtp({email, type: 'sign-in'});
  if (error) { out('Could not send a code: ' + errText(error)); return; }
  document.getElementById('verify').hidden = false;
  out('If this email can sign in, a code was sent. Check your inbox and spam.');
};
document.getElementById('verify').onsubmit = async (e) => {
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  const otp = document.getElementById('otp').value.trim();
  // The client adds the signed oauth_query; Neon then resumes the authorization.
  const { data, error } = await auth.signIn.emailOtp({email, otp});
  if (error) { out('Sign-in failed: ' + errText(error)); return; }
  if (data && data.url) { location.assign(data.url); return; }
  if (isOAuth) await resume(); else out('Signed in. Go back to Claude to continue.');
};
// Trades a returning neon_auth_session_verifier for a session, if present.
const { data } = await auth.getSession();
if (!isOAuth) out('Open this page from Claude by adding the connector.');
else if (data && data.session) await resume();
</script>"""

_CONSENT_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Allow access: kitchen-inventory</title><h1>Allow access to kitchen-inventory</h1>
<p><b id="client">…</b> wants access to the kitchen inventory.</p>
<p>It will be sent back to: <span id="hosts"></span></p><p id="loopback"></p>
<button id="allow" disabled>Allow</button> <button id="deny" disabled>Deny</button><p id="msg"></p>
<script type="module">%(common)s
const host = (u) => { try { return new URL(u).host; } catch (e) { return u; } };
const loop = (u) => { try { return ['localhost', '127.0.0.1', '[::1]'].includes(new URL(u).hostname); } catch (e) { return false; } };
async function respond(accept) {
  const { error } = await auth.oauth2.consent({accept});  // navigates on success
  if (error) out('Could not ' + (accept ? 'allow' : 'deny') + ': ' + errText(error));
}
document.getElementById('allow').onclick = () => respond(true);
document.getElementById('deny').onclick = () => respond(false);
await auth.getSession();
const { data: c, error } = await auth.oauth2.publicClient({query: {client_id: params.get('client_id') || ''}});
if (error || !c) out('Could not load the app asking for access: ' + (error ? errText(error) : 'empty reply'));
else {
  document.getElementById('client').textContent = c.client_name || c.client_id;
  const uris = c.redirect_uris || [];
  document.getElementById('hosts').textContent = uris.map(host).join(', ') || '(unknown)';
  if (uris.length && uris.every(loop))
    document.getElementById('loopback').textContent =
      'This app only sends you back to this device (localhost). Its name is not proof of who made it.';
}
document.getElementById('allow').disabled = false;
document.getElementById('deny').disabled = false;
</script>"""


def _page(template: str, config: AuthConfig) -> HTMLResponse:
    common = _COMMON_JS % {"client_js": CLIENT_JS, "issuer": json.dumps(config.issuer)}
    return HTMLResponse(template % {"common": common}, headers=_HEADERS)


def register_pages(mcp, config: AuthConfig) -> None:
    @mcp.custom_route("/login", methods=["GET"])
    async def login(request: Request):
        return _page(_LOGIN_HTML, config)

    @mcp.custom_route("/consent", methods=["GET"])
    async def consent(request: Request):
        return _page(_CONSENT_HTML, config)

    @mcp.custom_route(f"/static/{CLIENT_JS}", methods=["GET"])
    async def client_js(request: Request):
        return FileResponse(STATIC_DIR / CLIENT_JS, media_type="text/javascript",
                            headers={"Cache-Control": "public, max-age=3600"})
