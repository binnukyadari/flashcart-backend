"""Explicit one-shot reconciliation and outbox dispatch; no scheduler or deployment."""

import argparse
import json

from flashcart.bootstrap import checkout
from flashcart.cloud import GCSInvoices, PubSubPublisher
from flashcart.config import Settings
from flashcart.service import dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliver", action="store_true", help="Allow configured GCP writes")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not args.deliver or not 1 <= args.limit <= 100:
        parser.error("Pass --deliver and a --limit between 1 and 100")
    settings = Settings()
    if settings.mode != "gcp":
        parser.error("Worker requires FLASHCART_MODE=gcp")
    from google.cloud import pubsub_v1, storage

    svc = checkout(settings)
    publisher_client = pubsub_v1.PublisherClient()
    storage_client = storage.Client(project=settings.project_id)
    publisher = PubSubPublisher(publisher_client, settings.pubsub_topic)
    invoices = GCSInvoices(storage_client, settings.invoice_bucket)

    class Delivery:
        def publish(self, event):
            order = svc.orders.get(event.order_id)
            # An immutable order summary, not a tax invoice or payment receipt.
            content = json.dumps(order.model_dump(mode="json"), sort_keys=True).encode()
            invoices.put(order.id, content)
            publisher.publish(event)

    try:
        for order in svc.orders.pending(args.limit):
            svc.reconcile(order)
        print(f"Delivered {dispatch(svc.orders, Delivery(), args.limit)} event(s)")
    except Exception as exc:
        print(f"Delivery incomplete ({type(exc).__name__}); retry after resolving the dependency")
        raise SystemExit(1) from None
    finally:
        svc.inventory.close()
        svc.orders.close()
        publisher_client.stop()
        storage_client.close()


if __name__ == "__main__":
    main()
