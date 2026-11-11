import pytest
from fastapi.testclient import TestClient

from flashcart.api import create_app
from flashcart.config import Settings
from flashcart.memory import MemoryInventory, MemoryOrders
from flashcart.models import Product
from flashcart.service import Checkout


@pytest.fixture
def service():
    return Checkout(
        MemoryInventory(
            [
                Product(sku="A", name="Coffee", unit_price_cents=12500, available=5),
                Product(sku="B", name="Tea", unit_price_cents=5000, available=3),
            ]
        ),
        MemoryOrders(),
    )


@pytest.fixture
def client(service):
    with TestClient(create_app(Settings(mode="memory"), service)) as client:
        yield client


@pytest.fixture
def payload():
    return {"customer_id": "customer-1", "items": [{"sku": "A", "quantity": 2}]}
