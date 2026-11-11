from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from flashcart.models import Conflict, Order, Product, Reservation


class PostgresInventory:
    def __init__(self, dsn: str):
        self.pool = ConnectionPool(
            dsn,
            open=True,
            min_size=0,
            max_size=5,
            timeout=5,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": 5,
                "options": "-c statement_timeout=5000 -c lock_timeout=3000",
            },
        )

    def products(self, limit: int) -> list[Product]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM products ORDER BY sku LIMIT %s", (limit,)).fetchall()
            return [Product.model_validate(row) for row in rows]

    def reserve(self, order: Order) -> Reservation:
        fingerprint = order.request.fingerprint()
        with self.pool.connection() as conn, conn.transaction():
            # Serialize retries for this order even before its ledger row exists.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (order.id,))
            existing = conn.execute(
                "SELECT fingerprint, result FROM inventory_reservations WHERE order_id = %s",
                (order.id,),
            ).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise Conflict("Reservation input changed")
                return Reservation.model_validate(existing["result"])
            skus = sorted(item.sku for item in order.request.items)
            rows = conn.execute(
                "SELECT * FROM products WHERE sku = ANY(%s) ORDER BY sku FOR UPDATE", (skus,)
            ).fetchall()
            products = {row["sku"]: Product.model_validate(row) for row in rows}
            unavailable = any(
                item.sku not in products or products[item.sku].available < item.quantity
                for item in order.request.items
            )
            if unavailable:
                result = Reservation(status="rejected", total_cents=0, reason="unavailable_stock")
            else:
                total = sum(
                    products[item.sku].unit_price_cents * item.quantity
                    for item in order.request.items
                )
                for item in sorted(order.request.items, key=lambda item: item.sku):
                    conn.execute(
                        "UPDATE products SET available = available - %s WHERE sku = %s",
                        (item.quantity, item.sku),
                    )
                result = Reservation(status="confirmed", total_cents=total)
            conn.execute(
                "INSERT INTO inventory_reservations (order_id, fingerprint, result) "
                "VALUES (%s, %s, %s)",
                (order.id, fingerprint, Jsonb(result.model_dump())),
            )
            return result

    def ping(self):
        with self.pool.connection() as conn:
            # Check both required relations without changing data.
            conn.execute("SELECT sku FROM products LIMIT 0")
            conn.execute("SELECT order_id FROM inventory_reservations LIMIT 0")

    def close(self):
        self.pool.close()
