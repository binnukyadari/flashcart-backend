import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from flashcart.migrate import RUNTIME_USERS, bootstrap


def test_bootstrap_rejects_remote_host_and_wrong_database():
    for dsn in [
        "postgresql://user@remote.example/inventory",
        "postgresql://user@127.0.0.1/production",
    ]:
        with pytest.raises(ValueError):
            bootstrap(dsn, "")


@pytest.fixture
def bootstrap_database():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Requires a disposable local PostgreSQL cluster")
    if conninfo_to_dict(dsn).get("host") not in {"127.0.0.1", "localhost"}:
        pytest.fail("Bootstrap tests require a disposable loopback PostgreSQL cluster")
    # Never reuse or remove an existing database. Tests own only what they create.
    with psycopg.connect(dsn, autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname='inventory'").fetchone():
            pytest.fail("Disposable cluster must not already contain an inventory database")
        for user in RUNTIME_USERS:
            if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (user,)).fetchone():
                pytest.fail("Disposable cluster contains a runtime role already")
        admin.execute("CREATE DATABASE inventory")
        for user in RUNTIME_USERS:
            admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(user)))
        try:
            yield make_conninfo(dsn, dbname="inventory")
        finally:
            admin.execute("DROP DATABASE inventory")
            for user in RUNTIME_USERS:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(user)))


@pytest.mark.integration
def test_bootstrap_replay_preserves_stock_and_limits_runtime_privileges(bootstrap_database):
    dsn = bootstrap_database
    schema = Path("sql/001_inventory.sql").read_text()
    bootstrap(dsn, schema)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE products SET available=77 WHERE sku='DEMO-001'")
    bootstrap(dsn, schema)
    with psycopg.connect(dsn) as conn:
        assert (
            conn.execute("SELECT available FROM products WHERE sku='DEMO-001'").fetchone()[0] == 77
        )
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(RUNTIME_USERS[0])))
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 1
        conn.execute("UPDATE products SET available=76 WHERE sku='DEMO-001'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM products")
