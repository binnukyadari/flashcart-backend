from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from flashcart.api import create_app
from flashcart.config import Settings


def test_health_and_products(client):
    assert client.get("/health/live").json() == {"status": "ok", "mode": "memory"}
    assert client.get("/health/ready").status_code == 200
    products = client.get("/v1/products?limit=1").json()
    assert len(products) == 1
    assert products[0]["sku"] == "A"
    assert client.get("/v1/products?limit=101").status_code == 422


def test_create_get_replay_conflict(client, payload, service):
    headers = {"Idempotency-Key": "checkout-1"}
    created = client.post("/v1/orders", json=payload, headers=headers)
    assert created.status_code == 201
    order = created.json()
    assert order["status"] == "confirmed"
    assert order["total_cents"] == 25000
    assert client.get(created.headers["Location"]).json() == order
    replay = client.post("/v1/orders", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json() == order
    assert service.inventory.products(10)[0].available == 3
    assert len(service.orders.events(10)) == 1
    payload["items"][0]["quantity"] = 1
    assert client.post("/v1/orders", json=payload, headers=headers).status_code == 409


@pytest.mark.parametrize("quantity", [0, -1, 1001, 1.5, True, "2"])
def test_invalid_quantity(client, payload, quantity):
    payload["items"][0]["quantity"] = quantity
    assert (
        client.post(
            "/v1/orders", json=payload, headers={"Idempotency-Key": "checkout-1"}
        ).status_code
        == 422
    )


def test_bad_input_and_unknown_order(client, payload):
    assert client.post("/v1/orders", json=payload).status_code == 422
    assert (
        client.post(
            "/v1/orders", json=payload, headers={"Idempotency-Key": "../bad/key"}
        ).status_code
        == 422
    )
    payload["items"].append(payload["items"][0].copy())
    assert (
        client.post(
            "/v1/orders", json=payload, headers={"Idempotency-Key": "checkout-1"}
        ).status_code
        == 422
    )
    assert client.get("/v1/orders/not-a-uuid").status_code == 422
    assert client.get("/v1/orders/00000000-0000-0000-0000-000000000000").status_code == 404


def test_insufficient_stock_is_atomic(client, service):
    body = {
        "customer_id": "customer-1",
        "items": [
            {"sku": "A", "quantity": 1},
            {"sku": "B", "quantity": 4},
        ],
    }
    response = client.post("/v1/orders", json=body, headers={"Idempotency-Key": "stock-key"})
    assert response.status_code == 409
    assert response.json()["status"] == "rejected"
    assert response.json()["reason"] == "unavailable_stock"
    assert service.inventory.products(10)[0].available == 5
    assert service.orders.events(10) == []


def test_pending_after_inventory_commit_recovers_once(client, service, payload, monkeypatch):
    original = service.orders.finish

    def lost_firestore(*args):
        raise TimeoutError("secret backend connection details")

    monkeypatch.setattr(service.orders, "finish", lost_firestore)
    response = client.post("/v1/orders", json=payload, headers={"Idempotency-Key": "recover-key"})
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.headers["Retry-After"] == "5"
    assert "secret" not in response.text
    assert service.inventory.products(10)[0].available == 3
    monkeypatch.setattr(service.orders, "finish", original)
    replay = client.post("/v1/orders", json=payload, headers={"Idempotency-Key": "recover-key"})
    assert replay.status_code == 200
    assert replay.json()["id"] == response.json()["id"]
    assert replay.json()["status"] == "confirmed"
    assert service.inventory.products(10)[0].available == 3


def test_worker_can_reconcile_abandoned_pending(service, payload, monkeypatch):
    from flashcart.models import OrderRequest

    order, _ = service.orders.create("abandoned-key", OrderRequest.model_validate(payload))
    service.inventory.reserve(order)  # Simulate process crash after inventory commit.
    pending = service.orders.pending(100)
    assert len(pending) == 1
    service.reconcile(pending[0])
    assert service.orders.get(order.id).status == "confirmed"
    assert service.inventory.products(10)[0].available == 3


def test_concurrent_retries(client, service, payload):
    def submit(_):
        return client.post("/v1/orders", json=payload, headers={"Idempotency-Key": "parallel-key"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(16)))
    assert {r.status_code for r in responses} <= {200, 201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert service.inventory.products(10)[0].available == 3


def test_api_key_and_generic_dependency_error(service, payload, monkeypatch):
    key = "a" * 32
    app = create_app(Settings(mode="memory", api_key=key), service)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health/live").status_code == 200
        for path in ("/health/ready", "/v1/products"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"X-API-Key": key}).status_code == 200
        assert (
            client.post(
                "/v1/orders", json=payload, headers={"Idempotency-Key": "test-key"}
            ).status_code
            == 401
        )

        def fail(*args):
            raise RuntimeError("password=do-not-expose")

        monkeypatch.setattr(service.orders, "create", fail)
        response = client.post(
            "/v1/orders", json=payload, headers={"Idempotency-Key": "test-key", "X-API-Key": key}
        )
        assert response.status_code == 503
        assert "do-not-expose" not in response.text


def test_gcp_settings_fail_closed(monkeypatch):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(mode="gcp")
    with pytest.raises(ValidationError):
        Settings(mode="memory", api_key="short")


def test_item_order_is_ignored_for_retries(client):
    body = {
        "customer_id": "customer-1",
        "items": [
            {"sku": "A", "quantity": 1},
            {"sku": "B", "quantity": 1},
        ],
    }
    headers = {"Idempotency-Key": "reordered-key"}
    first = client.post("/v1/orders", json=body, headers=headers)
    body["items"].reverse()
    replay = client.post("/v1/orders", json=body, headers=headers)
    assert first.status_code == 201 and replay.status_code == 200
    assert first.json() == replay.json()


def test_prices_cannot_be_supplied_by_customer(client, payload):
    payload["items"][0]["unit_price_cents"] = 1
    assert (
        client.post(
            "/v1/orders", json=payload, headers={"Idempotency-Key": "price-tamper-key"}
        ).status_code
        == 422
    )
