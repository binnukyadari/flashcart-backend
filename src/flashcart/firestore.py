import hashlib

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from flashcart.models import (
    Conflict,
    Event,
    NotFound,
    Order,
    OrderRequest,
    Reservation,
    order_event,
)


class FirestoreOrders:
    def __init__(self, client: firestore.Client):
        self.client = client
        self.orders = client.collection("orders")
        self.keys = client.collection("idempotency")
        self.outbox = client.collection("outbox")

    def create(self, key: str, request: OrderRequest) -> tuple[Order, bool]:
        # Hash caller-controlled keys: no raw keys or path fragments in document IDs.
        key_ref = self.keys.document(hashlib.sha256(key.encode()).hexdigest())
        candidate = Order(request=request)

        @firestore.transactional
        def run(tx):
            existing = key_ref.get(transaction=tx)
            if existing.exists:
                data = existing.to_dict()
                if data["fingerprint"] != request.fingerprint():
                    raise Conflict("Idempotency key already used with different input")
                return self._get(data["order_id"], tx), False
            tx.create(self.orders.document(candidate.id), candidate.model_dump(mode="json"))
            tx.create(key_ref, {"fingerprint": request.fingerprint(), "order_id": candidate.id})
            return candidate, True

        return run(self.client.transaction(max_attempts=5))

    def _get(self, order_id: str, tx=None) -> Order:
        snapshot = self.orders.document(order_id).get(transaction=tx)
        if not snapshot.exists:
            raise NotFound("Order not found")
        return Order.model_validate(snapshot.to_dict())

    def get(self, order_id: str) -> Order:
        return self._get(order_id)

    def finish(self, order_id: str, reservation: Reservation) -> Order:
        @firestore.transactional
        def run(tx):
            order = self._get(order_id, tx)
            if order.status != "pending":
                return order
            order = order.model_copy(update=reservation.model_dump())
            tx.set(self.orders.document(order_id), order.model_dump(mode="json"))
            if order.status == "confirmed":
                event = order_event(order)
                tx.create(
                    self.outbox.document(event.id),
                    {
                        "event": event.model_dump(mode="json"),
                        "published": False,
                    },
                )
            return order

        return run(self.client.transaction(max_attempts=5))

    def pending(self, limit: int) -> list[Order]:
        query = self.orders.where(filter=FieldFilter("status", "==", "pending")).limit(limit)
        return [Order.model_validate(doc.to_dict()) for doc in query.stream()]

    def events(self, limit: int) -> list[Event]:
        query = self.outbox.where(filter=FieldFilter("published", "==", False)).limit(limit)
        return [Event.model_validate(doc.to_dict()["event"]) for doc in query.stream()]

    def mark_published(self, event_id: str):
        self.outbox.document(event_id).update({"published": True})

    def ping(self):
        # A real read, not a mutation or collection listing permission requirement.
        self.orders.document("_readiness").get(timeout=5, retry=None)

    def close(self):
        self.client.close()
