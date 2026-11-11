import sys
from unittest.mock import Mock

import pytest

from flashcart import worker
from flashcart.config import Settings
from flashcart.models import OrderRequest


def test_worker_requires_explicit_delivery_and_cloud_mode(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["flashcart-worker"])
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2
    monkeypatch.setattr(sys, "argv", ["flashcart-worker", "--deliver"])
    monkeypatch.setattr(worker, "Settings", lambda: Settings(mode="memory"))
    with pytest.raises(SystemExit) as exc:
        worker.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("fail_invoice", [False, True])
def test_worker_invoice_then_event_and_retry(monkeypatch, service, payload, fail_invoice):
    from google.cloud import pubsub_v1, storage

    order, _ = service.orders.create("worker-key", OrderRequest.model_validate(payload))
    settings = Mock(
        mode="gcp", project_id="demo-flashcart", pubsub_topic="topic", invoice_bucket="invoices"
    )
    pub_client, storage_client, publisher, invoices = Mock(), Mock(), Mock(), Mock()
    monkeypatch.setattr(sys, "argv", ["flashcart-worker", "--deliver", "--limit", "5"])
    monkeypatch.setattr(worker, "Settings", lambda: settings)
    monkeypatch.setattr(worker, "checkout", lambda _: service)
    monkeypatch.setattr(pubsub_v1, "PublisherClient", lambda: pub_client)
    monkeypatch.setattr(storage, "Client", lambda **_: storage_client)
    monkeypatch.setattr(worker, "PubSubPublisher", lambda *_: publisher)
    monkeypatch.setattr(worker, "GCSInvoices", lambda *_: invoices)
    if fail_invoice:
        invoices.put.side_effect = TimeoutError("not acknowledged")
        with pytest.raises(SystemExit) as exc:
            worker.main()
        assert exc.value.code == 1
        publisher.publish.assert_not_called()
        assert len(service.orders.events(10)) == 1
    else:

        def publish(event):
            invoices.put.assert_called_once()
            assert event.order_id == order.id

        publisher.publish.side_effect = publish
        worker.main()
        assert service.orders.events(10) == []
    assert service.orders.get(order.id).status == "confirmed"
    pub_client.stop.assert_called_once()
    storage_client.close.assert_called_once()
