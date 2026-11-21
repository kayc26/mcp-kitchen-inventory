"""Sign-in tests (task 15): token verification, allowlist, fail-closed startup,
access-log filter, and the pages.

Uses a locally generated Ed25519 key and a fake JWKS; no real tokens. The MCP
tests call /mcp on the real server app, so list_inventory reads the database
in DATABASE_URL (a test branch, never production).
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from starlette.testclient import TestClient

import kitchen_auth
import server
from conftest import TEST_AUTH_ENV

ISSUER = TEST_AUTH_ENV["AUTH_ISSUER"]
RESOURCE = TEST_AUTH_ENV["AUTH_RESOURCE"]
KEY = Ed25519PrivateKey.generate()
OTHER_KEY = Ed25519PrivateKey.generate()
ROOT = Path(__file__).resolve().parent.parent


class FakeJWKClient:
    """Stands in for PyJWKClient: knows one key id."""

    def get_signing_key_from_jwt(self, token):
        if jwt.get_unverified_header(token).get("kid") != "k1":
            raise jwt.PyJWKClientError("unknown kid")
        return type("SigningKey", (), {"key": KEY.public_key()})()


def _token(key=KEY, kid="k1", drop=(), **overrides) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": [RESOURCE, f"{ISSUER}/oauth2/userinfo"], "sub": "user-allowed",
              "client_id": "https://claude.ai/oauth/claude-code-client-metadata", "scope": "openid",
              "iat": now, "exp": now + 3600}
    claims.update(overrides)
    for k in drop:
        claims.pop(k)
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid})


def _verifier() -> kitchen_auth.NeonTokenVerifier:
    return kitchen_auth.NeonTokenVerifier(ISSUER, RESOURCE, "unused", jwk_client=FakeJWKClient())


def _verify(token):
    return asyncio.run(_verifier().verify_token(token))


# --- Verifier -----------------------------------------------------------------

def test_valid_token_accepted():
    result = _verify(_token())
    assert result is not None and result.subject == "user-allowed"


def test_aud_as_single_string_accepted():
    assert _verify(_token(aud=RESOURCE)) is not None


@pytest.mark.parametrize("token", [
    _token(iss="https://ep-other.neonauth.example.test/neondb/auth"),
    _token(iss="https://issuer.test"),                      # the origin alone isn't the issuer
    _token(aud="https://elsewhere.test/mcp"),               # wrong audience, single
    _token(aud=["https://elsewhere.test/mcp", f"{ISSUER}/oauth2/userinfo"]),  # wrong audience, list
    _token(exp=int(time.time()) - 10),                      # expired
    _token(drop=("exp",)),                                  # no expiry
    _token(drop=("sub",)),                                  # missing sub
    _token(sub=""),                                         # empty sub
    _token(key=OTHER_KEY),                                  # bad signature
    _token(kid="k-unknown"),                                # unknown kid
    "not-a-jwt",
], ids=["wrong-iss", "iss-origin-only", "wrong-aud-single", "wrong-aud-list", "expired", "no-exp",
        "missing-sub", "empty-sub", "bad-signature", "unknown-kid", "garbage"])
def test_bad_tokens_rejected(token):
    assert _verify(token) is None


def test_rejection_log_has_no_token(capsys):
    token = _token(key=OTHER_KEY)
    _verify(token)
    out = capsys.readouterr().out
    assert "token_rejected" in out and token not in out


def test_jwks_cached_and_refetched_on_unknown_kid(monkeypatch):
    """The real PyJWKClient: one fetch for repeated tokens, a refetch when a
    token names a key it hasn't seen (key rotation)."""
    jwks = {"keys": [{**OKPAlgorithm.to_jwk(KEY.public_key(), as_dict=True), "kid": "k1"}]}
    calls = []
    client = jwt.PyJWKClient("https://issuer.test/jwks.json", cache_keys=True)
    monkeypatch.setattr(client, "fetch_data", lambda: calls.append(1) or jwks)
    verifier = kitchen_auth.NeonTokenVerifier(ISSUER, RESOURCE, "unused", jwk_client=client)

    assert asyncio.run(verifier.verify_token(_token())) is not None
    assert asyncio.run(verifier.verify_token(_token())) is not None
    assert len(calls) == 1

    jwks["keys"].append({**OKPAlgorithm.to_jwk(OTHER_KEY.public_key(), as_dict=True), "kid": "k2"})
    assert asyncio.run(verifier.verify_token(_token(key=OTHER_KEY, kid="k2"))) is not None
    assert len(calls) == 2


# --- /mcp on the real server app ----------------------------------------------

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}


@pytest.fixture(scope="module")
def client():
    server.mcp._token_verifier.jwk_client = FakeJWKClient()  # fake JWKS for the real verifier
    # One client for the module: the MCP session manager can only start once per process.
    with TestClient(server.build_app()) as c:
        yield c


def _bearer(token):
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def _rpc_result(resp) -> dict:
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        data = [line[5:].strip() for line in resp.text.splitlines() if line.startswith("data:")]
        return json.loads(data[-1])
    return resp.json()


def test_no_token_401_with_metadata_header(client):
    r = client.post("/mcp", json=INIT, headers=HEADERS)
    assert r.status_code == 401
    assert ('resource_metadata="https://kitchen.test/.well-known/oauth-protected-resource/mcp"'
            in r.headers["www-authenticate"])


def test_invalid_token_401(client):
    r = client.post("/mcp", json=INIT, headers=_bearer(_token(key=OTHER_KEY)))
    assert r.status_code == 401


def test_metadata_names_issuer(client):
    body = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert body["resource"] == RESOURCE
    assert [s.rstrip("/") for s in body["authorization_servers"]] == [ISSUER]


def test_valid_token_not_allowed_403(client, capsys):
    r = client.post("/mcp", json=INIT, headers=_bearer(_token(sub="user-stranger")))
    assert r.status_code == 403
    assert r.json()["error"] == "forbidden"
    log = capsys.readouterr().out
    assert '"auth": "not_allowed"' in log and '"sub": "user-stranger"' in log


def test_allowed_token_list_inventory_works(client):
    headers = _bearer(_token(sub="user-also-allowed"))
    r = client.post("/mcp", json=INIT, headers=headers)
    assert r.status_code == 200, r.text
    session = {"mcp-session-id": r.headers["mcp-session-id"]} if "mcp-session-id" in r.headers else {}
    headers = {**headers, **session, "mcp-protocol-version": "2025-06-18"}
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    assert r.status_code in (200, 202)
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": "list_inventory", "arguments": {}}}, headers=headers)
    assert r.status_code == 200, r.text
    result = _rpc_result(r)["result"]
    assert not result.get("isError"), result
    assert result["content"][0]["text"] == server.list_inventory()


def test_stats_answers_without_token(client):
    r = client.get("/stats")
    assert r.status_code == 200
    assert r.json()["server"] == "kitchen-inventory"


# --- Pages --------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/login", "/consent"])
def test_pages_block_framing(client, path):
    r = client.get(path + "?client_id=x&response_type=code")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert '"/static/neon-auth-client.js"' in r.text
    assert json.dumps(ISSUER) in r.text
    assert "google" not in r.text.lower()


def test_login_page_doesnt_reveal_accounts(client):
    assert "If this email can sign in, a code was sent" in client.get("/login").text


def test_vendored_client_served(client):
    r = client.get("/static/neon-auth-client.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert "createAuthClient" in r.text or "neon_auth_session_verifier" in r.text


# --- Access log ---------------------------------------------------------------

def test_access_log_drops_query_strings():
    kitchen_auth.install_access_log_filter()
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/login?client_id=x&sig=SECRET&neon_auth_session_verifier=V", "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(record)
    assert record.getMessage() == '1.2.3.4:5 - "GET /login HTTP/1.1" 200'


# --- Fail closed ----------------------------------------------------------------

def _start_server(env_overrides: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in TEST_AUTH_ENV and k != "DATABASE_URL"}
    env.update({k: v for k, v in {**TEST_AUTH_ENV, **env_overrides}.items() if v is not None})
    return subprocess.run([sys.executable, "-c", "import server"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("name", kitchen_auth.REQUIRED_ENV)
@pytest.mark.parametrize("value", [None, "", "  "], ids=["unset", "empty", "blank"])
def test_startup_refuses_without_env_var(name, value):
    result = _start_server({name: value})
    assert result.returncode != 0
    assert "refuses to start" in result.stderr and name in result.stderr


def test_startup_refuses_with_only_commas_in_allowlist():
    result = _start_server({"ALLOWED_USER_IDS": " , ,"})
    assert result.returncode != 0 and "ALLOWED_USER_IDS" in result.stderr
