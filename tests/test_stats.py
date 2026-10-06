"""GET /stats tests. Runs against the database in DATABASE_URL.

Only touches test-named items in a test-named location and removes them
afterwards. The database may hold real items too, so counts are compared
before and after adding them. "Today" is pinned.
"""
import asyncio
import datetime
import uuid

import pytest
from starlette.testclient import TestClient

import server

ITEM = f"test-stats-{uuid.uuid4().hex[:8]}"
LOCATION = f"test-stats-loc-{uuid.uuid4().hex[:8]}"
TODAY = datetime.date(2026, 9, 27)


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch):
    monkeypatch.setattr(server, "_today", lambda: TODAY)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    for loc, items in server._get_items().items():
        for name in items:
            if ITEM in name:
                server._delete_item(loc, name)
    server._delete_location(LOCATION)


@pytest.fixture
def client():
    return TestClient(server.mcp.streamable_http_app())


def _add(suffix: str, expiry: str) -> None:
    server._upsert_item(LOCATION, f"{ITEM}-{suffix}", {
        "quantity": "1", "details": "", "expiry": expiry,
        "made_date": "", "low_threshold": "",
    })


def _stats(client) -> dict:
    resp = client.get("/stats")
    assert resp.status_code == 200
    return resp.json()


def test_response_shape(client):
    body = _stats(client)
    assert set(body) == {"server", "as_of", "tools", "totals", "monthly"}
    assert body["server"] == "kitchen-inventory"
    assert body["as_of"] == TODAY.isoformat()
    assert set(body["totals"]) == {"items_tracked", "locations", "expired_now"}
    assert all(isinstance(v, int) for v in body["totals"].values())
    assert body["monthly"] == []


def test_no_row_content(client):
    _add("secret", "2026-01-01")
    resp = client.get("/stats")
    assert ITEM not in resp.text and LOCATION not in resp.text


def test_counts_on_fixture(client):
    before = _stats(client)["totals"]
    _add("past", "2026-09-26")       # yesterday: expired
    _add("today", "2026-09-27")      # expires today: not expired yet
    _add("future", "2026-10-30")
    _add("none", "")                 # no expiry: not expired
    after = _stats(client)["totals"]
    assert after["items_tracked"] - before["items_tracked"] == 4
    assert after["locations"] - before["locations"] == 1
    assert after["expired_now"] - before["expired_now"] == 1


def test_tools_matches_registry(client):
    assert _stats(client)["tools"] == len(asyncio.run(server.mcp.list_tools()))


def test_post_not_allowed(client):
    assert client.post("/stats").status_code == 405


def test_no_cors_headers(client):
    resp = client.get("/stats", headers={"Origin": "https://example.com"})
    assert not any(h.lower().startswith("access-control-") for h in resp.headers)


def test_db_error_returns_500_without_data(client, monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(server, "_get_conn", boom)
    resp = client.get("/stats")
    assert resp.status_code == 500
    assert resp.text == "stats unavailable"
