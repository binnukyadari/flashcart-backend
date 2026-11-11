"""Explicit, non-destructive dev schema bootstrap; never called at API startup."""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

RUNTIME_USERS = (
    "flashcart-api@ritru-fc-dev-01.iam",
    "flashcart-worker@ritru-fc-dev-01.iam",
)


def bootstrap(dsn: str, schema: str):
    config = conninfo_to_dict(dsn)
    if config.get("host") not in {"127.0.0.1", "localhost"} or config.get("dbname") != "inventory":
        raise ValueError("Dev bootstrap requires the local proxy and inventory database")
    # The connection context commits schema, grants and seed together.
    schema = schema.replace("BEGIN;", "").replace("COMMIT;", "")
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        conn.execute(schema)
        for user in RUNTIME_USERS:
            conn.execute(
                sql.SQL("GRANT SELECT, UPDATE ON products TO {}").format(sql.Identifier(user))
            )
            conn.execute(
                sql.SQL("GRANT SELECT, INSERT ON inventory_reservations TO {}").format(
                    sql.Identifier(user)
                )
            )
        conn.execute(
            "INSERT INTO products (sku, name, unit_price_cents, available, currency) "
            "VALUES ('DEMO-001', 'Demo coffee', 25000, 100, 'INR') ON CONFLICT (sku) DO NOTHING"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-dev", action="store_true", required=True)
    parser.parse_args()
    path = Path(sys.prefix) / "share/flashcart/sql/001_inventory.sql"
    try:
        bootstrap(os.environ["FLASHCART_BOOTSTRAP_DATABASE_URL"], path.read_text())
        print("Dev inventory schema, runtime grants and non-overwriting demo seed are ready.")
    except Exception as error:
        print(f"Dev bootstrap failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
