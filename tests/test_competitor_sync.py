"""Panel-driven synchronization does not require a separate parser process."""
from unittest.mock import patch


def test_sync_requires_session_and_csrf(client, authed):
    other = client.application.test_client()
    assert other.post("/api/competitor/sync").status_code == 401
    client, _ = authed
    assert client.post("/api/competitor/sync").status_code == 403


def test_sync_updates_prices_and_history_and_throttles(authed):
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    with patch("competitors.fetch_catalogue", return_value=[{"name": "Кофе", "price_cents": 10000}]) as fetch:
        assert client.post("/api/competitor/sync", headers=headers).get_json()["created"] == 1
        assert client.post("/api/competitor/sync", headers=headers).get_json()["ok"] is True
        fetch.assert_called_once_with(client.application.config["COMPETITOR_SOURCE_URL"])
    client.application.extensions["competitor_sync"]["checked_at"] = None
    with patch("competitors.fetch_catalogue", return_value=[{"name": "Кофе", "price_cents": 11000}]):
        assert client.post("/api/competitor/sync", headers=headers).get_json()["changed"] == 1
    assert client.get("/api/competitor/comparison").get_json()["items"][0]["last_price_cents"] == 11000
    assert len(client.get("/api/competitor/history").get_json()["items"]) == 1


def test_sync_failure_preserves_saved_prices_and_retries(authed):
    client, csrf = authed
    headers = {"X-CSRF-Token": csrf}
    with patch("competitors.fetch_catalogue", return_value=[{"name": "Кофе", "price_cents": 10000}]):
        client.post("/api/competitor/sync", headers=headers)
    state = client.application.extensions["competitor_sync"]
    state["checked_at"] = None
    with patch("competitors.fetch_catalogue", side_effect=OSError("offline")) as fetch:
        assert client.post("/api/competitor/sync", headers=headers).get_json() == {"ok": False}
        assert client.post("/api/competitor/sync", headers=headers).get_json() == {"ok": False}
        assert fetch.call_count == 1
    assert client.get("/api/competitor/comparison").get_json()["items"][0]["last_price_cents"] == 10000
    state["checked_at"] = None
    with patch("competitors.fetch_catalogue", return_value=[{"name": "Кофе", "price_cents": 12000}]):
        assert client.post("/api/competitor/sync", headers=headers).get_json()["ok"] is True
