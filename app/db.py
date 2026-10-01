"""PostgreSQL access. Credentials come from the Vault secret
(keys: db_username / db_password); the pool is rebuilt when they rotate."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import psycopg.sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import metrics
from .config import settings
from .vault_client import SecretSnapshot

log = logging.getLogger("beacon.db")


def conninfo_from_secret(snap: SecretSnapshot) -> str:
    user = snap.get("db_username")
    password = snap.get("db_password")
    if not user or not password:
        raise ValueError("vault secret is missing db_username / db_password")
    return psycopg.conninfo.make_conninfo(
        host=snap.get("db_host", settings.db_host),
        port=snap.get("db_port", str(settings.db_port)),
        dbname=snap.get("db_name", settings.db_name),
        user=user,
        password=password,
        sslmode=settings.db_sslmode,
        application_name=f"{settings.app_name}-{settings.pod_name}"[:63],
        connect_timeout=5,
    )


def _set_search_path(conn: psycopg.Connection) -> None:
    # Set with SQL, not the libpq startup "options": the EDB PGD connection
    # manager forwards that value as one quoted identifier ("beacon,public").
    conn.execute(
        psycopg.sql.SQL("SET search_path TO {}, public").format(
            psycopg.sql.Identifier(settings.db_schema)
        )
    )


class Database:
    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None
        self.schema_version = 0

    def configure(self, snap: SecretSnapshot) -> None:
        """Called by the Vault store whenever the secret version changes."""
        new_pool = ConnectionPool(
            conninfo_from_secret(snap),
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=_set_search_path,
            open=False,
            name="beacon",
        )
        new_pool.open(wait=False)
        with self._lock:
            old, self._pool = self._pool, new_pool
        if old is not None:
            log.info("database credentials rotated; replacing pool",
                     extra={"secret_version": snap.version})
            old.close()
        else:
            log.info("database pool created",
                     extra={"db_host": snap.get("db_host", settings.db_host),
                            "db_name": snap.get("db_name", settings.db_name)})

    @property
    def configured(self) -> bool:
        return self._pool is not None

    @contextmanager
    def conn(self, operation: str) -> Iterator[psycopg.Connection]:
        pool = self._pool
        if pool is None:
            raise RuntimeError("database not configured (vault secret not loaded yet)")
        start = time.perf_counter()
        try:
            with pool.connection(timeout=5) as c:
                yield c
        finally:
            metrics.DB_QUERY_LATENCY.labels(operation=operation).observe(
                time.perf_counter() - start
            )
            stats = pool.get_stats()
            metrics.DB_POOL_SIZE.labels(state="size").set(stats.get("pool_size", 0))
            metrics.DB_POOL_SIZE.labels(state="available").set(
                stats.get("pool_available", 0)
            )

    def check(self) -> bool:
        try:
            with self.conn("health") as c:
                row = c.execute(
                    "SELECT coalesce(max(version), 0) AS v FROM schema_migrations"
                ).fetchone()
                self.schema_version = int(row["v"])
                metrics.SCHEMA_VERSION.set(self.schema_version)
                count = c.execute("SELECT count(*) AS n FROM items").fetchone()["n"]
                metrics.ITEMS.set(count)
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()


db = Database()
