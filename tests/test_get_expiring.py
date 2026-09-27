"""get_expiring tests. Runs against the database in DATABASE_URL.

Only touches test-named items in "pantry" and removes them afterwards. The
database may hold real items too, so results are filtered to test items and
counts are compared before and after adding them. "Today" is pinned.
"""
import datetime
import types
import uuid

import pytest

import server

ITEM = f"test-expiring-{uuid.uuid4().hex[:8]}"
TODAY = datetime.date(2026, 9, 27)
REAL_TODAY = server._today


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


def _add(suffix: str, expiry: str) -> str:
    name = f"{ITEM}-{suffix}"
    server.add_item(name, location="pantry", expiry=expiry)
    return name


def _in_days(n: int) -> str:
    return (TODAY + datetime.timedelta(days=n)).isoformat()


def _test_items(result: dict) -> list[dict]:
    return [i for i in result["items"] if ITEM in i["name"]]


def test_boundary_day_included_next_day_excluded():
    edge = _add("edge", _in_days(7))
    _add("after", _in_days(8))
    got = _test_items(server.get_expiring(days=7))
    assert [i["name"] for i in got] == [edge]
    assert got[0]["days_left"] == 7


def test_item_fields_and_sort_order():
    later = _add("later", _in_days(3))
    sooner = _add("sooner", _in_days(0))
    result = server.get_expiring(days=7)
    assert result["today"] == TODAY.isoformat()
    got = _test_items(result)
    assert [i["name"] for i in got] == [sooner, later]
    assert got[0] == {
        "name": sooner,
        "location": "pantry",
        "quantity": "1",
        "expiry": _in_days(0),
        "days_left": 0,
    }


def test_expired_items_included_by_default():
    expired = _add("expired", _in_days(-2))
    fresh = _add("fresh", _in_days(1))
    got = _test_items(server.get_expiring(days=7))
    assert [(i["name"], i["days_left"]) for i in got] == [(expired, -2), (fresh, 1)]


def test_expired_items_left_out_without_include_expired():
    _add("expired", _in_days(-2))
    today_item = _add("today", _in_days(0))
    got = _test_items(server.get_expiring(days=7, include_expired=False))
    assert [i["name"] for i in got] == [today_item]


def test_no_expiry_excluded_and_counted():
    before = server.get_expiring(days=7)
    _add("none-a", "")
    _add("none-b", "")
    after = server.get_expiring(days=7)
    assert _test_items(after) == []
    assert after["no_expiry_count"] == before["no_expiry_count"] + 2


def test_unparseable_expiry_excluded_and_counted():
    before = server.get_expiring(days=7)
    _add("bad", "next week")
    after = server.get_expiring(days=7)
    assert _test_items(after) == []
    assert after["unparseable_count"] == before["unparseable_count"] + 1
    assert after["no_expiry_count"] == before["no_expiry_count"]


def test_today_uses_eastern_time(monkeypatch):
    # 02:00 UTC on Sep 28 is still Sep 27 in New York.
    utc_now = datetime.datetime(2026, 9, 28, 2, 0, tzinfo=datetime.timezone.utc)

    class FakeDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return utc_now.astimezone(tz)

    monkeypatch.setattr(server, "datetime", types.SimpleNamespace(
        datetime=FakeDatetime, date=datetime.date, timedelta=datetime.timedelta,
    ))
    assert REAL_TODAY() == datetime.date(2026, 9, 27)
