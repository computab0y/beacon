"""Fetch the app's secret from Vault using the pod's ServiceAccount.

Flow:
  1. Read the projected ServiceAccount JWT (audience "vault") from disk.
  2. POST it to auth/<mount>/login with role=<VAULT_ROLE>. Vault calls the
     OKD TokenReview API to confirm the SA name/namespace, then returns a
     short-lived token carrying the "beacon" policy.
  3. Read KV v2 <kv_mount>/data/<path>.
  4. Re-read periodically; on change, notify listeners (e.g. rebuild DB pool).

Secret values are never logged - only key names and the KV version.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import hvac
from hvac.exceptions import Forbidden, InvalidRequest, VaultError

from . import metrics
from .config import settings

log = logging.getLogger("beacon.vault")


@dataclass(frozen=True)
class SecretSnapshot:
    data: dict[str, str]
    version: int
    fetched_at: float

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.data.get(key, default)


class VaultSecretStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: hvac.Client | None = None
        self._token_expires_at = 0.0
        self._snapshot: SecretSnapshot | None = None
        self._listeners: list[Callable[[SecretSnapshot], None]] = []
        self.last_error: str | None = None

    # ---------- public API ----------
    @property
    def snapshot(self) -> SecretSnapshot | None:
        return self._snapshot

    def on_change(self, fn: Callable[[SecretSnapshot], None]) -> None:
        self._listeners.append(fn)

    def healthy(self) -> bool:
        snap = self._snapshot
        if snap is None:
            return False
        # Stale if we have failed to refresh for 3 intervals.
        return (time.time() - snap.fetched_at) < settings.secret_refresh_seconds * 3

    def refresh(self) -> SecretSnapshot:
        """Login if needed, read the secret, fire listeners if it changed."""
        with self._lock:
            try:
                snap = self._read_with_relogin()
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                metrics.ERRORS.labels(type="vault").inc()
                log.error("failed to read secret from vault", exc_info=True,
                          extra={"vault_path": self._full_path(),
                                 "vault_role": settings.vault_role})
                raise
            changed = self._snapshot is None or snap.version != self._snapshot.version
            self._snapshot = snap
            self.last_error = None
        if changed:
            log.info(
                "loaded secret from vault",
                extra={"vault_path": self._full_path(), "secret_version": snap.version,
                       "secret_keys": sorted(snap.data)},
            )
            for fn in self._listeners:
                try:
                    fn(snap)
                except Exception:
                    metrics.ERRORS.labels(type="secret_listener").inc()
                    log.error("secret change listener failed", exc_info=True)
        return snap

    # ---------- internals ----------
    def _read_with_relogin(self) -> SecretSnapshot:
        had_token = self._client is not None
        try:
            return self._read_secret()
        except (Forbidden, InvalidRequest):
            if not had_token:
                raise  # the login itself was refused - nothing to retry
            # Cached token revoked/expired early - force one fresh login.
            log.warning("vault token rejected, re-authenticating")
            self._client = None
            return self._read_secret()

    def _full_path(self) -> str:
        return f"{settings.vault_kv_mount}/data/{settings.vault_secret_path}"

    def _verify(self):
        if settings.vault_cacert is None:
            return True
        if settings.vault_cacert.lower() == "false":
            return False
        return settings.vault_cacert

    def _authenticated_client(self) -> hvac.Client:
        now = time.time()
        if self._client is not None and now < self._token_expires_at - 30:
            return self._client

        client = hvac.Client(
            url=settings.vault_addr,
            namespace=settings.vault_namespace,
            verify=self._verify(),
            timeout=10,
        )
        if settings.vault_dev_token:
            client.token = settings.vault_dev_token
            ttl = 3600
            log.warning("using VAULT_DEV_TOKEN - local development only")
        else:
            jwt = Path(settings.vault_jwt_path).read_text().strip()
            try:
                resp = client.auth.kubernetes.login(
                    role=settings.vault_role,
                    jwt=jwt,
                    mount_point=settings.vault_auth_mount,
                )
            except VaultError:
                metrics.VAULT_LOGINS.labels(result="failure").inc()
                raise
            metrics.VAULT_LOGINS.labels(result="success").inc()
            auth = resp["auth"]
            ttl = int(auth.get("lease_duration") or 0)
            log.info(
                "authenticated to vault via kubernetes auth",
                extra={"vault_role": settings.vault_role,
                       "vault_policies": auth.get("policies"),
                       "token_ttl_seconds": ttl},
            )
        self._client = client
        # ttl 0 means "no expiry" (root/dev); treat as 1h for refresh purposes.
        self._token_expires_at = now + (ttl or 3600)
        metrics.VAULT_TOKEN_TTL.set(ttl)
        return client

    def _read_secret(self) -> SecretSnapshot:
        client = self._authenticated_client()
        try:
            resp = client.secrets.kv.v2.read_secret_version(
                path=settings.vault_secret_path,
                mount_point=settings.vault_kv_mount,
                raise_on_deleted_version=True,
            )
        except Exception:
            metrics.VAULT_SECRET_READS.labels(result="failure").inc()
            raise
        metrics.VAULT_SECRET_READS.labels(result="success").inc()
        data = resp["data"]["data"] or {}
        version = int(resp["data"]["metadata"]["version"])
        now = time.time()
        metrics.VAULT_SECRET_VERSION.set(version)
        metrics.VAULT_LAST_SUCCESS.set(now)
        metrics.VAULT_TOKEN_TTL.set(max(0, int(self._token_expires_at - now)))
        return SecretSnapshot(data={k: str(v) for k, v in data.items()},
                              version=version, fetched_at=now)


store = VaultSecretStore()
