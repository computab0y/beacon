"""Beacon - a small FastAPI service demonstrating the OKD platform path:
Vault (k8s auth) -> PG4K -> Prometheus metrics -> JSON logs -> Loki -> Grafana.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field

from . import metrics
from .config import settings
from .db import db
from .logging_setup import configure_logging, request_id_var
from .vault_client import store

configure_logging()
log = logging.getLogger("beacon")

CHAOS_ENABLED = os.getenv("ENABLE_CHAOS_ENDPOINTS", "true").lower() == "true"
_QUIET_PATHS = {"/metrics", "/healthz/live", "/healthz/ready"}


# ---------------------------------------------------------------- background
class HealthLoop(threading.Thread):
    """Refreshes the Vault secret and probes the DB; drives the health gauges."""

    def __init__(self) -> None:
        super().__init__(name="health-loop", daemon=True)
        self.stop_event = threading.Event()
        self._prev: dict[str, bool] = {}
        self.state: dict[str, bool] = {"vault": False, "database": False}
        self.started_at = time.time()

    @property
    def ready(self) -> bool:
        return all(self.state.values())

    def tick(self) -> None:
        snap = store.snapshot
        if snap is None or time.time() - snap.fetched_at >= settings.secret_refresh_seconds:
            try:
                store.refresh()
            except Exception:
                pass  # already logged + counted inside refresh()
        vault_ok = store.healthy()
        db_ok = db.configured and db.check()
        self._set("vault", vault_ok, store.last_error)
        self._set("database", db_ok, db.last_error)
        metrics.UP.set(1 if (vault_ok and db_ok) else 0)

    def _set(self, component: str, ok: bool, err: str | None) -> None:
        metrics.HEALTH.labels(component=component).set(1 if ok else 0)
        self.state[component] = ok
        prev = self._prev.get(component)
        if prev is not ok:  # log transitions only, not every tick
            if ok:
                log.info("dependency healthy", extra={"component": component})
            else:
                metrics.ERRORS.labels(type=f"{component}_unhealthy").inc()
                log.error("dependency unhealthy",
                          extra={"component": component, "error": err})
        self._prev[component] = ok

    def run(self) -> None:
        backoff = 1
        while not self.stop_event.is_set():
            self.tick()
            healthy = self.ready
            # Retry quickly (with backoff) while not ready, steady interval after.
            wait = settings.health_interval_seconds if healthy else min(backoff, 30)
            backoff = 1 if healthy else backoff * 2
            self.stop_event.wait(wait)


health_loop = HealthLoop()


@asynccontextmanager
async def lifespan(_: FastAPI):
    metrics.START_TIME.set(health_loop.started_at)
    metrics.UP.set(0)
    for c in ("vault", "database"):
        metrics.HEALTH.labels(component=c).set(0)
    store.on_change(db.configure)
    log.info("starting", extra={"commit": settings.git_commit,
                                "namespace": settings.namespace,
                                "vault_addr": settings.vault_addr})
    if not settings.disable_background:
        health_loop.start()
    yield
    log.info("shutting down")
    health_loop.stop_event.set()
    db.close()


app = FastAPI(title="beacon", version=settings.version, lifespan=lifespan)


# ---------------------------------------------------------------- middleware
@app.middleware("http")
async def observe(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    metrics.HTTP_IN_FLIGHT.inc()
    status = 500
    try:
        try:
            response = await call_next(request)
        except Exception as exc:  # unhandled error in a route
            metrics.ERRORS.labels(type=type(exc).__name__).inc()
            db_down = isinstance(exc, (psycopg.OperationalError, PoolTimeout))
            log.error("database unavailable" if db_down else "unhandled exception",
                      exc_info=exc,
                      extra={"path": request.url.path, "method": request.method})
            response = JSONResponse(
                status_code=503 if db_down else 500,
                content={"error": "database_unavailable" if db_down else "internal_error",
                         "request_id": rid})
        status = response.status_code
        response.headers["x-request-id"] = rid
        response.headers["x-app-version"] = settings.version
        return response
    finally:
        duration = time.perf_counter() - start
        metrics.HTTP_IN_FLIGHT.dec()
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        metrics.HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
        metrics.HTTP_LATENCY.labels(request.method, route_path).observe(duration)
        # Access log. 4xx/5xx are WARNING here; the root cause is logged once at
        # ERROR where it happened, so counting ERROR lines in Loki = real errors.
        if request.url.path in _QUIET_PATHS:
            level = logging.DEBUG
        elif status >= 400:
            level = logging.WARNING
        else:
            level = logging.INFO
        log.log(level, "request", extra={
            "method": request.method, "path": request.url.path, "route": route_path,
            "status": status, "duration_ms": round(duration * 1000, 2),
            "client": request.client.host if request.client else None,
        })
        request_id_var.reset(token)


# ---------------------------------------------------------------- endpoints
@app.get("/")
def root():
    return {"app": settings.app_name, "version": settings.version,
            "commit": settings.git_commit, "pod": settings.pod_name}


@app.get("/healthz/live")
def live():
    return {"status": "alive"}


@app.get("/healthz/ready")
def ready():
    vault_ok = health_loop.state["vault"]
    db_ok = health_loop.state["database"]
    body = {"status": "ready" if vault_ok and db_ok else "not_ready",
            "vault": vault_ok, "database": db_ok}
    return JSONResponse(status_code=200 if vault_ok and db_ok else 503, content=body)


@app.get("/health")
def health():
    snap = store.snapshot
    return {
        "app": settings.app_name, "version": settings.version,
        "commit": settings.git_commit, "pod": settings.pod_name,
        "uptime_seconds": round(time.time() - health_loop.started_at, 1),
        "vault": {"healthy": store.healthy(), "secret_version": snap.version if snap else None,
                  "last_error": store.last_error},
        "database": {"healthy": health_loop.state["database"],
                     "schema_version": db.schema_version,
                     "last_error": db.last_error},
    }


@app.get("/metrics")
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/secret-info")
def secret_info():
    """Proves the Vault secret was read - shows key names and version, never values."""
    snap = store.snapshot
    if snap is None:
        raise HTTPException(503, "secret not loaded yet")
    return {"path": f"{settings.vault_kv_mount}/{settings.vault_secret_path}",
            "version": snap.version, "keys": sorted(snap.data),
            "greeting": snap.get("greeting")}  # a deliberately non-sensitive key


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


@app.get("/api/items")
def list_items(limit: int = 50):
    with db.conn("list_items") as c:
        rows = c.execute(
            "SELECT id, name, description, created_by, created_at FROM items"
            " ORDER BY id DESC LIMIT %s", (min(max(limit, 1), 500),)).fetchall()
    return rows


@app.post("/api/items", status_code=201)
def create_item(item: ItemIn):
    with db.conn("create_item") as c, c.transaction():
        row = c.execute(
            "INSERT INTO items(name, description, created_by) VALUES (%s, %s, %s)"
            " RETURNING id, name, description, created_by, created_at",
            (item.name, item.description, settings.pod_name)).fetchone()
        c.execute(
            "INSERT INTO audit_events(kind, detail, pod, version) VALUES (%s, %s, %s, %s)",
            ("item_created", json.dumps({"id": row["id"]}), settings.pod_name, settings.version))
    log.info("item created", extra={"item_id": row["id"]})
    return row


@app.post("/api/simulate-error")
def simulate_error(kind: str = "runtime"):
    """Generate an error on purpose to exercise the metrics/Loki/Grafana path."""
    if not CHAOS_ENABLED:
        raise HTTPException(404)
    if kind == "handled":
        metrics.ERRORS.labels(type="simulated_handled").inc()
        log.error("simulated handled error", extra={"kind": kind})
        return JSONResponse(status_code=502, content={"error": "simulated"})
    raise RuntimeError(f"simulated {kind} failure")
