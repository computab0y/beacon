"""Apply versioned SQL migrations from ./migrations to the PG4K database.

Run as an Argo CD Sync hook Job (same image, same ServiceAccount), so the
schema is in place before the new Deployment rolls out. Uses a Postgres
advisory lock so concurrent runs are safe, and stores a checksum per version
so an edited, already-applied migration fails loudly instead of drifting.

    python -m app.migrate
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
from pathlib import Path

import psycopg

from .config import settings
from .db import conninfo_from_secret
from .logging_setup import configure_logging
from .vault_client import VaultSecretStore

log = logging.getLogger("beacon.migrate")
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
FILE_RE = re.compile(r"^V(\d+)__([\w-]+)\.sql$")
LOCK_ID = 7_314_159_265  # arbitrary, app-specific


def discover() -> list[tuple[int, str, Path]]:
    found = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = FILE_RE.match(p.name)
        if not m:
            raise ValueError(f"bad migration filename: {p.name} (want V001__name.sql)")
        found.append((int(m.group(1)), m.group(2), p))
    versions = [v for v, _, _ in found]
    if len(versions) != len(set(versions)):
        raise ValueError("duplicate migration versions")
    return sorted(found)


def run(conninfo: str) -> int:
    schema = settings.db_schema
    applied_now = 0
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            conn.execute(f'SET search_path TO "{schema}", public')
            conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version     integer PRIMARY KEY,
                       name        text NOT NULL,
                       checksum    text NOT NULL,
                       applied_by  text NOT NULL,
                       applied_at  timestamptz NOT NULL DEFAULT now())"""
            )
            done = {
                r[0]: r[1]
                for r in conn.execute("SELECT version, checksum FROM schema_migrations")
            }
            for version, name, path in discover():
                sql = path.read_text()
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if version in done:
                    if done[version] != checksum:
                        raise RuntimeError(
                            f"migration V{version:03d} was modified after being applied"
                        )
                    continue
                log.info("applying migration", extra={"migration": path.name})
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, name, checksum, applied_by)"
                        " VALUES (%s, %s, %s, %s)",
                        (version, name, checksum, f"{settings.app_name}@{settings.version}"),
                    )
                applied_now += 1
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied_now


def main() -> int:
    configure_logging()
    try:
        snap = VaultSecretStore().refresh()
        n = run(conninfo_from_secret(snap))
        log.info("migrations complete", extra={"applied": n})
        return 0
    except Exception:
        log.error("migration failed", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
