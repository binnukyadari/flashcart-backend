from flashcart.models import Order, OrderRequest
from flashcart.ports import Inventory, Orders, Publisher


class Checkout:
    def __init__(self, inventory: Inventory, orders: Orders):
        self.inventory, self.orders = inventory, orders

    def submit(self, key: str, request: OrderRequest) -> tuple[Order, bool]:
        order, created = self.orders.create(key, request)
        return self.reconcile(order), created

    def reconcile(self, order: Order) -> Order:
        if order.status != "pending":
            return order
        # The reservation ledger survives a lost response or failed Firestore finish.
        # Never compensate blindly: the database transaction may already have committed.
        reservation = self.inventory.reserve(order)
        return self.orders.finish(order.id, reservation)


def dispatch(orders: Orders, publisher: Publisher, limit: int = 100) -> int:
    count = 0
    for event in orders.events(limit):
        publisher.publish(event)
        # Mark only after acknowledgement. A crash here can duplicate delivery;
        # consumers must deduplicate by the stable event.id.
        orders.mark_published(event.id)
        count += 1
    return count
