import hashlib
from uuid import UUID

from google.api_core.exceptions import PreconditionFailed

from flashcart.models import Conflict, Event


class PubSubPublisher:
    def __init__(self, client, topic: str):
        self.client, self.topic = client, topic

    def publish(self, event: Event):
        self.client.publish(
            self.topic,
            event.model_dump_json().encode(),
            event_id=event.id,
            event_type=event.type,
        ).result(timeout=15)


class GCSInvoices:
    """Private, immutable invoice artifacts; no public ACLs or signed URLs."""

    def __init__(self, client, bucket: str):
        self.bucket = client.bucket(bucket)

    def put(self, order_id: str, content: bytes) -> str:
        name = f"invoices/{UUID(order_id)}.json"
        blob = self.bucket.blob(name)
        digest = hashlib.sha256(content).hexdigest()
        blob.metadata = {"sha256": digest}
        try:
            blob.upload_from_string(
                content, content_type="application/json", if_generation_match=0, timeout=15
            )
        except PreconditionFailed:
            # Creation may have succeeded before an earlier response was lost.
            blob.reload(timeout=15)
            if (blob.metadata or {}).get("sha256") != digest:
                raise Conflict("Invoice already exists with different content") from None
        return f"gs://{self.bucket.name}/{name}"
