from threading import RLock

from flashcart.models import (
    Conflict,
    Event,
    NotFound,
    Order,
    OrderRequest,
    Product,
    Reservation,
    order_event,
)


class MemoryInventory:
    """Single-process demo adapter; never used as a cloud fallback."""

    def __init__(self, products: list[Product] | None = None):
        self._lock = RLock()
        self._products = {p.sku: p for p in products or []}
        self._ledger: dict[str, tuple[str, Reservation]] = {}

    def products(self, limit: int) -> list[Product]:
        with self._lock:
            return [self._products[k] for k in sorted(self._products)[:limit]]

    def reserve(self, order: Order) -> Reservation:
        with self._lock:
            fingerprint = order.request.fingerprint()
            if existing := self._ledger.get(order.id):
                if existing[0] != fingerprint:
                    raise Conflict("Reservation input changed")
                return existing[1]
            result = Reservation(status="confirmed", total_cents=0)
            for item in order.request.items:
                p = self._products.get(item.sku)
                if p is None or p.available < item.quantity:
                    result = Reservation(
                        status="rejected", total_cents=0, reason="unavailable_stock"
                    )
                    break
            if result.status == "confirmed":
                total = 0
                for item in order.request.items:
                    p = self._products[item.sku]
                    total += p.unit_price_cents * item.quantity
                    self._products[p.sku] = p.model_copy(
                        update={"available": p.available - item.quantity}
                    )
                result = Reservation(status="confirmed", total_cents=total)
            self._ledger[order.id] = (fingerprint, result)
            return result

    def ping(self):
        pass

    def close(self):
        pass


class MemoryOrders:
    def __init__(self):
        self._lock = RLock()
        self._orders: dict[str, Order] = {}
        self._keys: dict[str, str] = {}
        self._events: dict[str, Event] = {}

    def create(self, key: str, request: OrderRequest) -> tuple[Order, bool]:
        with self._lock:
            if order_id := self._keys.get(key):
                order = self._orders[order_id]
                if order.request.fingerprint() != request.fingerprint():
                    raise Conflict("Idempotency key already used with different input")
                return order, False
            order = Order(request=request)
            self._orders[order.id] = order
            self._keys[key] = order.id
            return order, True

    def get(self, order_id: str) -> Order:
        with self._lock:
            if order_id not in self._orders:
                raise NotFound("Order not found")
            return self._orders[order_id]

    def finish(self, order_id: str, reservation: Reservation) -> Order:
        with self._lock:
            order = self.get(order_id)
            if order.status == "pending":
                order = order.model_copy(update=reservation.model_dump())
                self._orders[order_id] = order
                if order.status == "confirmed":
                    event = order_event(order)
                    self._events[event.id] = event
            return order

    def pending(self, limit: int) -> list[Order]:
        with self._lock:
            return [o for o in self._orders.values() if o.status == "pending"][:limit]

    def events(self, limit: int) -> list[Event]:
        with self._lock:
            return list(self._events.values())[:limit]

    def mark_published(self, event_id: str):
        with self._lock:
            self._events.pop(event_id, None)

    def ping(self):
        pass

    def close(self):
        pass
