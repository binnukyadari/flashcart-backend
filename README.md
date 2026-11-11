# FlashCart backend

FastAPI foundation for a small FlashCart order and inventory service. Python 3.12,
PostgreSQL inventory, Firestore orders and idempotency, Pub/Sub events and private
GCS invoice artifacts. This repository contains application code only. It neither
provisions infrastructure nor deploys anything to GCP.

## Run locally without credentials

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip install --no-deps -e .
uvicorn flashcart.api:create_app --factory --host 127.0.0.1 --port 8080
```

Default `FLASHCART_MODE=memory` seeds ten `DEMO-001` coffee products at 25000
paise each. Memory mode loses all data on restart and supports one process only.
It never loads cloud credentials or silently falls back from a failed cloud service.
Interactive API docs: <http://127.0.0.1:8080/docs>.

```bash
curl http://127.0.0.1:8080/v1/products
curl -i http://127.0.0.1:8080/v1/orders \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-checkout-1' \
  -d '{"customer_id":"customer-1","items":[{"sku":"DEMO-001","quantity":2}]}'
```

Repeat the same request and key to receive the same order without a second stock
deduction. Keys are 8–128 letters, digits, underscores or hyphens. Reusing a key
with different input returns 409. Keys are scoped to this trusted service, not to
browser sessions; callers must use a globally unique key for each intended order.
Line order is ignored for idempotency; duplicate SKUs and caller-supplied prices
are rejected. Prices come from inventory and use integer paise, with INR currency.

| Endpoint | Behavior |
| --- | --- |
| `GET /health/live` | Process liveness and selected mode; no dependency calls |
| `GET /health/ready` | Inventory schema and order store read check |
| `GET /v1/products?limit=50` | Up to 100 products with stock and prices |
| `POST /v1/orders` | 201 confirmed, 200 confirmed replay, 202 pending recovery, 409 rejected stock or key conflict |
| `GET /v1/orders/{uuid}` | Durable order snapshot; 404 if absent |

An order response includes its status and `Location`. A pending response includes
`Retry-After: 5`; retry **POST with the original key and body** to resume processing,
or run the reconciler. GET only reads. If a dependency fails before order intent
is acknowledged, the API returns generic 503; retry the same key. Infrastructure
exceptions, connection strings, keys and request bodies are not included in API errors.

## Consistency and recovery

1. A Firestore transaction creates the pending order and idempotency document.
   A different request for an existing key cannot create another order.
2. PostgreSQL locks the order reservation key, then product rows in SKU order.
   Stock changes and a permanent reservation result commit in one transaction.
   Competing orders cannot oversell. A shortage rejects the entire basket.
3. A Firestore transaction records the confirmed/rejected result. Confirmation
   also creates one durable outbox event with a stable event ID.
4. A separate explicit worker resumes pending orders and drains the outbox. It
   creates an immutable private JSON invoice artifact, waits for the Pub/Sub publish
   acknowledgement, then marks the event published.

There is no distributed transaction between PostgreSQL and Firestore. If the
reservation commits but the response or final order write is lost, the order
stays pending. A retry reads the committed reservation result, even after a process
restart. It never blindly reserves again or refunds stock. Keep reservation and
idempotency records for the full lifetime of accepted orders; no TTL is configured.
Rejected reservations are also permanent for that order ID.

Outbox delivery is **at least once**. A crash after publish but before marking it
published can send the same event again. Consumers must durably deduplicate
`event.id` together with their own effects. Example envelope:

```json
{"id":"order-created-<uuid>","type":"order.created.v1","order_id":"<uuid>","occurred_at":"<UTC timestamp>","schema_version":1}
```

No customer data is included in the event. Confirmed here means inventory reserved,
not paid or shipped. Invoice artifacts are JSON order summaries, **not statutory tax
invoices or payment receipts**. Their deterministic UUID paths and GCS generation
preconditions prevent overwrites; an existing different digest is a conflict.
Only trusted writers should have bucket write access.

## GCP integration configuration (not deployment)

Set `FLASHCART_MODE=gcp` plus the variables listed in [.env.example](.env.example).
The app reads environment variables; it does not load `.env` automatically. Cloud
mode requires every integration setting and an API key of at least 32 characters.
Use `X-API-Key` for every endpoint except liveness (docs/schema remain visible).
This key authenticates trusted service clients; there is no customer login,
per-customer authorization, payment or cancellation workflow. Do not expose this
foundation as a public customer checkout API.

- **Cloud SQL PostgreSQL:** supply a psycopg PostgreSQL connection string through
  `FLASHCART_DATABASE_URL`. For remote TCP, use certificate-verified TLS; a separately
  managed Cloud SQL Auth Proxy can provide a local connection. Networking, IAM and
  the proxy are external responsibilities. The bounded pool uses at most five
  connections per API process and five per worker. Account for instance scaling.
  No migrations, database creation or seed writes happen at startup. A schema owner
  must deliberately apply `sql/001_inventory.sql` and load catalog data. Use a runtime
  role with SELECT/UPDATE on products and SELECT/INSERT on inventory_reservations;
  no schema-owner privileges. This initial SQL is a versioned bootstrap migration;
  changes need new migrations, not edits to a live schema.
- **Firestore:** Application Default Credentials (ADC), explicit project and database.
  Collections: `orders`, SHA-256-keyed `idempotency`, and `outbox`. Transactions contain
  Firestore operations only. Runtime server SDK access is controlled by IAM, not
  client security rules. No client-facing Firestore access is needed.
- **Pub/Sub:** full topic path `projects/<project>/topics/<topic>` in the configured
  project. Grant the worker publish access only to this topic. The API never publishes
  inline. There is no push endpoint or subscriber yet.
- **GCS:** existing private bucket name. The worker needs object create/read access
  to `invoices/`; it never sets ACLs, makes objects public or generates signed URLs.
  GCS bucket access must enforce the intended privacy and retention policy externally.

Use ADC/workload identity on GCP and inject the API key and database connection
through Secret Manager when deploying later. Never commit service-account JSON,
passwords, `.env` files or tokens. GCP emulator variables such as
`FIRESTORE_EMULATOR_HOST` must be absent from a real runtime.

Only after explicitly configuring the intended environment, the one-shot worker
can perform cloud writes:

```bash
flashcart-worker --deliver --limit 100
```

The worker has no automatic scheduler. It exits on a dependency error and leaves
work recoverable; retry after resolving the cause. Multiple workers are safe but
can duplicate event delivery. Failed oldest items can delay a batch; dead-letter
handling, backoff scheduling and production operations remain future work. Readiness
checks cover PostgreSQL and Firestore; worker access to Pub/Sub/GCS needs a separate
live qualification. No live GCP qualification is implied by local tests.

## Test and verify

```bash
ruff check .
ruff format --check .
pytest -m 'not integration'
```

For real PostgreSQL transaction tests, use a **disposable local database**. The tests
truncate their tables and add/remove a constraint. Never point `TEST_DATABASE_URL`
at shared data or GCP. Firestore tests require a local emulator and use isolated
`demo-*` projects, anonymous credentials and no cloud access.

```bash
docker run --detach --name flashcart-test-postgres \
  --publish 127.0.0.1:15432:5432 \
  --env POSTGRES_USER=flashcart --env POSTGRES_PASSWORD=local-test-only \
  --env POSTGRES_DB=flashcart postgres:17-alpine
# Separate terminal: requires Java 17+, curl, Python 3; checksum-verified Google emulator.
bash scripts/start-firestore-emulator.sh
# Test terminal:
TEST_DATABASE_URL=postgresql://flashcart:local-test-only@127.0.0.1:15432/flashcart \
FIRESTORE_EMULATOR_HOST=127.0.0.1:18080 \
pytest --cov=flashcart --cov-report=term-missing --cov-fail-under=80
docker rm --force flashcart-test-postgres
```

Tests cover validation, authentication, conflicting and concurrent retries, stock
shortages, actual PostgreSQL rollback and contention, Firestore transactions and
outbox behavior, restart recovery, and mocked Pub/Sub/GCS timeout/precondition
boundaries. Integration tests **skip**, rather than pass, when their local services
are missing. CI starts both local services, runs all tests and builds/smokes the image.
The workflow has read-only GitHub permissions and no GCP credentials or deployment steps.

## Container

```bash
docker build -t flashcart-backend:local .
docker run --rm --name flashcart-local --read-only --tmpfs /tmp \
  --publish 127.0.0.1:8080:8080 flashcart-backend:local
# Another terminal:
docker exec -i flashcart-local python < scripts/container-smoke.py
```

The image runs as UID 10001, honors `PORT` (default 8080), uses a multi-stage build,
and copies only allowlisted application inputs. No cloud credentials are embedded.
Runtime dependencies and development tools are hash-locked separately. To deliberately
refresh them using Python 3.12:

```bash
uv pip compile --python-version 3.12 --universal --generate-hashes -o requirements.lock pyproject.toml
uv pip compile --python-version 3.12 --universal --generate-hashes --extra=dev -o requirements-dev.lock pyproject.toml
```

This is a tested foundation. Live cloud integration, deployment, customer identity,
payments, cancellations/stock release, tax invoices and operational hardening remain
separate milestones. `flashcart-gcp-infra` is not modified by this repository.

## Dev training deployment

The fork `binnukyadari/flashcart-backend` adds two cloud-only entry points while
keeping the default local memory demo unchanged:

- `python -m flashcart.cloudrun api`: starts the private Cloud SQL Auth Proxy and
  API together. The proxy uses workload identity and automatic IAM DB login.
- `python -m flashcart.cloudrun worker`: runs one bounded delivery batch, then
  stops the proxy and exits. It does not leave a sidecar running forever.
- `python -m flashcart.cloudrun migrate`: explicitly bootstraps the dev inventory
  schema using a separate Secret Manager database credential. It grants the two
  runtime IAM users only the needed table permissions and inserts `DEMO-001`
  only when absent. Repeating this step never resets stock or deletes orders.

`FLASHCART_SQL_INSTANCE` must equal the existing dev instance. The bootstrap is
never executed at API startup. Cloud commands are explicit; the regular image
entry point still supports the credential-free local memory demo.

`.github/workflows/ci.yml` tests the app with disposable PostgreSQL and Firestore
emulators and smoke-tests the container. `.github/workflows/image.yml` calls
those checks before publishing an immutable, source/run-tagged linux/amd64 image
to `asia-south1-docker.pkg.dev/ritru-fc-dev-01/flashcart-docker/backend`. The job
summary records the digest. Publishing an image does not deploy Cloud Run.

The corresponding infra PR adds a separate dev backend stack. The registry,
network and existing database stack are preserved. The API is authenticated;
this is a trusted-client training demo, not a public customer checkout system.
The existing `orders-created-v1` topic uses a different legacy schema. The demo
uses a separate topic matching this backend's versioned event envelope rather
than altering that topic or its consumers.

Google's [Cloud SQL Auth Proxy documentation](https://docs.cloud.google.com/sql/docs/postgres/connect-auth-proxy)
explains the private network and IAM connection. Runtime credentials belong in
Secret Manager, never in source, Docker images, plan logs or training examples.
