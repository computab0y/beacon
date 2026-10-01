"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path


def _read_version() -> str:
    env = os.getenv("APP_VERSION")
    if env:
        return env
    vfile = Path(__file__).resolve().parent.parent / "VERSION"
    return vfile.read_text().strip() if vfile.exists() else "0.0.0-dev"


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "beacon")
    version: str = field(default_factory=_read_version)
    git_commit: str = os.getenv("GIT_COMMIT", "unknown")
    pod_name: str = os.getenv("POD_NAME", socket.gethostname())
    namespace: str = os.getenv("POD_NAMESPACE", "local")
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # --- Vault (Kubernetes auth method) ---
    vault_addr: str = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
    vault_namespace: str | None = os.getenv("VAULT_NAMESPACE") or None
    vault_auth_mount: str = os.getenv("VAULT_AUTH_MOUNT", "kubernetes")
    vault_role: str = os.getenv("VAULT_ROLE", "beacon")
    vault_kv_mount: str = os.getenv("VAULT_KV_MOUNT", "secret")
    vault_secret_path: str = os.getenv("VAULT_SECRET_PATH", "beacon/config")
    vault_jwt_path: str = os.getenv(
        "VAULT_JWT_PATH", "/var/run/secrets/vault/token"
    )
    # Path to a CA bundle, or "false" to skip verification (lab only).
    vault_cacert: str | None = os.getenv("VAULT_CACERT") or None
    # Optional: a static token for local development only (bypasses k8s auth).
    vault_dev_token: str | None = os.getenv("VAULT_DEV_TOKEN") or None
    secret_refresh_seconds: int = int(os.getenv("SECRET_REFRESH_SECONDS", "300"))

    # --- PostgreSQL (EDB Postgres for Kubernetes) ---
    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "beacon")
    db_sslmode: str = os.getenv("DB_SSLMODE", "require")
    db_schema: str = os.getenv("DB_SCHEMA", "beacon")
    db_pool_min: int = int(os.getenv("DB_POOL_MIN", "1"))
    db_pool_max: int = int(os.getenv("DB_POOL_MAX", "5"))

    health_interval_seconds: int = int(os.getenv("HEALTH_INTERVAL_SECONDS", "15"))
    disable_background: bool = _bool("BEACON_DISABLE_BACKGROUND")


settings = Settings()
