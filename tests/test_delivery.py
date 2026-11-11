from unittest.mock import Mock
from uuid import uuid4

import pytest
from google.api_core.exceptions import PreconditionFailed

from flashcart.cloud import GCSInvoices, PubSubPublisher
from flashcart.models import Conflict, Order, OrderRequest, order_event
from flashcart.service import dispatch


def test_publish_ack_before_mark_and_stable_retries(service, payload):
    order, _ = service.submit("delivery-key", OrderRequest.model_validate(payload))
    publisher = Mock()
    publisher.publish.side_effect = TimeoutError("lost acknowledgement")
    with pytest.raises(TimeoutError):
        dispatch(service.orders, publisher)
    event = service.orders.events(10)[0]
    assert event.id == f"order-created-{order.id}"
    publisher.publish.side_effect = None
    assert dispatch(service.orders, publisher) == 1
    assert publisher.publish.call_args_list[0] == publisher.publish.call_args_list[1]
    assert dispatch(service.orders, publisher) == 0


def test_publish_waits_for_ack(payload):
    client = Mock()
    event = order_event(Order(request=OrderRequest.model_validate(payload)))
    PubSubPublisher(client, "projects/demo/topics/orders").publish(event)
    args, kwargs = client.publish.call_args
    assert args[0] == "projects/demo/topics/orders"
    assert kwargs["event_id"] == event.id
    client.publish.return_value.result.assert_called_once_with(timeout=15)


def test_invoice_create_only_replay_and_conflict():
    client = Mock()
    client.bucket.return_value.name = "private-invoices"
    blob = client.bucket.return_value.blob.return_value
    adapter = GCSInvoices(client, "private-invoices")
    order_id = str(uuid4())
    assert adapter.put(order_id, b"{}") == f"gs://private-invoices/invoices/{order_id}.json"
    assert blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    assert blob.upload_from_string.call_args.kwargs["content_type"] == "application/json"
    blob.upload_from_string.side_effect = PreconditionFailed("exists")
    assert adapter.put(order_id, b"{}").endswith(".json")

    def different_existing(**kwargs):
        blob.metadata = {"sha256": "different"}

    blob.reload.side_effect = different_existing
    with pytest.raises(Conflict):
        adapter.put(order_id, b"{}")
    with pytest.raises(ValueError):
        adapter.put("../escape", b"{}")
