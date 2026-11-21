"""Test-wide auth settings. The server refuses to start without them, so they're
set before any test imports `server`. Fake values: tests that verify tokens
use a locally generated key and a fake JWKS (see test_auth.py); nothing here
reaches a real issuer.
"""
import os

TEST_AUTH_ENV = {
    "AUTH_ISSUER": "https://issuer.test/neondb/auth",
    "AUTH_RESOURCE": "https://kitchen.test/mcp",
    "AUTH_JWKS_URL": "https://issuer.test/neondb/auth/.well-known/jwks.json",
    "ALLOWED_USER_IDS": "user-allowed, user-also-allowed",
}
os.environ.update(TEST_AUTH_ENV)
