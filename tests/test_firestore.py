import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore

from flashcart.firestore import FirestoreOrders
from flashcart.models import Conflict, OrderRequest, Reservation

pytestmark = pytest.mark.integration


@pytest.fixture
def orders():
    if not os.getenv("FIRESTORE_EMULATOR_HOST"):
        pytest.skip("Set FIRESTORE_EMULATOR_HOST to a local emulator")
    client = firestore.Client(
        project=f"demo-{uuid4().hex[:12]}", credentials=AnonymousCredentials()
    )
    adapter = FirestoreOrders(client)
    yield adapter
    adapter.close()


def test_concurrent_create_finish_and_outbox(orders, payload):
    request = OrderRequest.model_validate(payload)
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda _: orders.create("shared-key", request), range(12)))
    assert len({order.id for order, _ in created}) == 1
    assert sum(is_new for _, is_new in created) == 1
    order = created[0][0]
    assert orders.pending(100) == [order]
    reservation = Reservation(status="confirmed", total_cents=25000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        completed = list(pool.map(lambda _: orders.finish(order.id, reservation), range(12)))
    assert all(o.status == "confirmed" for o in completed)
    assert orders.pending(100) == []
    events = orders.events(100)
    assert len(events) == 1
    assert events[0].order_id == order.id
    orders.mark_published(events[0].id)
    assert orders.events(100) == []
    assert orders.get(order.id).total_cents == 25000


def test_key_conflict_and_rejected_no_event(orders, payload):
    request = OrderRequest.model_validate(payload)
    order, _ = orders.create("test-key", request)
    payload["customer_id"] = "changed"
    with pytest.raises(Conflict):
        orders.create("test-key", OrderRequest.model_validate(payload))
    result = orders.finish(
        order.id, Reservation(status="rejected", total_cents=0, reason="unavailable_stock")
    )
    assert result.status == "rejected"
    assert orders.events(100) == []


def test_adapter_restart_reuses_idempotency(orders, payload):
    request = OrderRequest.model_validate(payload)
    order, _ = orders.create("restart-key", request)
    restarted = FirestoreOrders(
        firestore.Client(project=orders.client.project, credentials=AnonymousCredentials())
    )
    try:
        assert restarted.create("restart-key", request) == (order, False)
    finally:
        restarted.close()
