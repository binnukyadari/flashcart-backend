-- Apply deliberately with a schema-owner account, never automatically at API startup.
BEGIN;
CREATE TABLE IF NOT EXISTS products (
    sku varchar(64) PRIMARY KEY,
    name text NOT NULL,
    unit_price_cents bigint NOT NULL CHECK (unit_price_cents >= 0),
    available integer NOT NULL CHECK (available >= 0),
    currency text NOT NULL DEFAULT 'INR' CHECK (currency = 'INR')
);
CREATE TABLE IF NOT EXISTS inventory_reservations (
    order_id uuid PRIMARY KEY,
    fingerprint char(64) NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMIT;
