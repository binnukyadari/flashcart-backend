import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from flashcart.models import Conflict, Order, OrderRequest
from flashcart.postgres import PostgresInventory

pytestmark = pytest.mark.integration


@pytest.fixture
def inventory():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to a disposable local PostgreSQL database")
    if conninfo_to_dict(dsn).get("host") not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("Destructive database tests require a loopback host")
    # This fixture is intentionally destructive; never point at a shared/cloud database.
    with psycopg.connect(dsn) as conn:
        conn.execute(Path("sql/001_inventory.sql").read_text())
        conn.execute("TRUNCATE inventory_reservations, products")
        conn.execute("INSERT INTO products VALUES ('A', 'Coffee', 12500, 5, 'INR')")
        conn.execute("INSERT INTO products VALUES ('B', 'Tea', 5000, 3, 'INR')")
    adapter = PostgresInventory(dsn)
    yield adapter
    adapter.close()


def order(quantity=2, sku="A"):
    return Order(
        request=OrderRequest(customer_id="test", items=[{"sku": sku, "quantity": quantity}])
    )


def test_concurrent_replay_and_restart(inventory):
    candidate = order()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: inventory.reserve(candidate), range(16)))
    assert all(r.status == "confirmed" and r.total_cents == 25000 for r in results)
    assert inventory.products(10)[0].available == 3
    restarted = PostgresInventory(os.environ["TEST_DATABASE_URL"])
    try:
        assert restarted.reserve(candidate) == results[0]
        assert restarted.products(10)[0].available == 3
    finally:
        restarted.close()


def test_concurrent_buyers_do_not_oversell(inventory):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: inventory.reserve(order(1)), range(12)))
    assert sum(r.status == "confirmed" for r in results) == 5
    assert inventory.products(10)[0].available == 0


def test_atomic_rejection_and_ledger_conflict(inventory):
    candidate = Order(
        request=OrderRequest(
            customer_id="test",
            items=[
                {"sku": "A", "quantity": 1},
                {"sku": "B", "quantity": 4},
            ],
        )
    )
    assert inventory.reserve(candidate).status == "rejected"
    assert inventory.products(10)[0].available == 5
    changed = candidate.model_copy(update={"request": order(1).request})
    with pytest.raises(Conflict):
        inventory.reserve(changed)
    assert inventory.reserve(order(sku="MISSING")).status == "rejected"


def test_rollback_on_ledger_write_failure(inventory):
    # Reject the ledger insert after stock update: both changes must roll back.
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        conn.execute("ALTER TABLE inventory_reservations ADD CONSTRAINT test_reject CHECK (false)")
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            inventory.reserve(order())
        assert inventory.products(10)[0].available == 5
    finally:
        with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
            conn.execute("ALTER TABLE inventory_reservations DROP CONSTRAINT test_reject")


def test_cross_store_restart_after_reservation(inventory, payload):
    from uuid import uuid4

    from google.auth.credentials import AnonymousCredentials
    from google.cloud import firestore

    from flashcart.firestore import FirestoreOrders
    from flashcart.service import Checkout

    if not os.getenv("FIRESTORE_EMULATOR_HOST"):
        pytest.skip("Cross-store test also requires the Firestore emulator")
    project = f"demo-{uuid4().hex[:12]}"
    first = FirestoreOrders(firestore.Client(project=project, credentials=AnonymousCredentials()))
    request = OrderRequest.model_validate(payload)
    candidate, _ = first.create("restart-key", request)
    inventory.reserve(candidate)
    first.close()  # Process dies before finishing the Firestore order.
    second = FirestoreOrders(firestore.Client(project=project, credentials=AnonymousCredentials()))
    restarted = PostgresInventory(os.environ["TEST_DATABASE_URL"])
    try:
        svc = Checkout(restarted, second)
        result, created = svc.submit("restart-key", request)
        assert not created
        assert result.id == candidate.id and result.status == "confirmed"
        assert restarted.products(10)[0].available == 3
        assert len(second.events(100)) == 1
    finally:
        second.close()
        restarted.close()
