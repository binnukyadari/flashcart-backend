from flashcart.config import Settings
from flashcart.memory import MemoryInventory, MemoryOrders
from flashcart.models import Product
from flashcart.service import Checkout


def checkout(settings: Settings) -> Checkout:
    if settings.mode == "memory":
        return Checkout(
            MemoryInventory(
                [Product(sku="DEMO-001", name="Demo coffee", unit_price_cents=25000, available=10)]
            ),
            MemoryOrders(),
        )
    from google.cloud import firestore

    from flashcart.firestore import FirestoreOrders
    from flashcart.postgres import PostgresInventory

    orders = FirestoreOrders(
        firestore.Client(project=settings.project_id, database=settings.firestore_database)
    )
    try:
        inventory = PostgresInventory(settings.database_url.get_secret_value())
    except Exception:
        orders.close()
        raise
    return Checkout(inventory, orders)
