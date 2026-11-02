"""The standalone parser delivers complete snapshots without leaking credentials."""
import io
import json
from unittest.mock import patch

import pytest

import db
from scripts.competitor_parser import parse_catalogue, sync_once
from tests.test_competitors import TOKEN


def catalogue(price):
    return ('<table><tr class="product" data-product-id="1">'
            '<td class="product-name">Кофе <b>зерновой</b></td>'
            f'<td class="product-price" data-price-kopecks="{price}">цена</td>'
            '</tr></table>')


def test_parse_catalogue():
    assert parse_catalogue(catalogue(89000)) == [
        {"name": "Кофе зерновой", "price_cents": 89000}
    ]


@pytest.mark.parametrize("html", ["", "<html>unavailable</html>", catalogue(0),
                                  catalogue("1.5"), catalogue(10**20),
                                  catalogue(100) + catalogue(200)])
def test_invalid_catalogue(html):
    with pytest.raises(ValueError):
        parse_catalogue(html)


def test_delivery_creates_comparison_and_price_history(authed, app):
    client, csrf = authed
    app.config["PANEL_API_KEY"] = TOKEN
    response = client.post("/api/products", json={"name": "Кофе зерновой", "price_cents": 90000},
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 201
    price = 89000

    class Opener:
        def open(self, request, timeout):
            assert timeout == 10
            if request.full_url == "http://source.test/":
                assert not request.has_header("Authorization")
                return io.BytesIO(catalogue(price).encode())
            assert request.full_url == "http://panel.test/api/competitor/prices"
            assert request.get_header("Authorization") == f"Bearer {TOKEN}"
            response = client.post("/api/competitor/prices", json=json.loads(request.data),
                                   headers={"Authorization": f"Bearer {TOKEN}"})
            assert response.status_code == 200
            return io.BytesIO(response.data)

    with patch("scripts.competitor_parser.build_opener", return_value=Opener()):
        assert sync_once("http://source.test/", "http://panel.test", TOKEN)["created"] == 1
        item = client.get("/api/competitor/comparison").get_json()["items"][0]
        assert item["my_product_name"] == "Кофе зерновой"
        assert item["my_price_cents"] is not None
        assert item["difference_cents"] == item["my_price_cents"] - price
        price = 95000
        assert sync_once("http://source.test/", "http://panel.test", TOKEN)["changed"] == 1
        assert sync_once("http://source.test/", "http://panel.test", TOKEN)["changed"] == 0
    history = client.get("/api/competitor/history").get_json()["items"]
    assert len(history) == 1
    assert (history[0]["old_price_cents"], history[0]["new_price_cents"]) == (89000, 95000)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    snapshot = client.get("/api/integrations/competitor-prices", headers=headers).get_json()[0]
    assert snapshot["own_price_cents"] == item["my_price_cents"]

    # A manual mapping must win over the automatic name match.
    client.post(f'/api/competitor/products/{item["id"]}/mapping',
                json={"my_product_id": 1}, headers={"X-CSRF-Token": csrf})
    mapped = client.get("/api/competitor/comparison").get_json()["items"][0]
    assert mapped["my_product_name"] == db.PRODUCT_CATALOGUE[0][0]
