"""Prometheus metrics. Scraped by OKD user-workload monitoring via ServiceMonitor.

Pod count / pod status come from two places:
  * count(beacon_build_info) - one series per running, scraped pod (works everywhere)
  * kube-state-metrics (kube_pod_status_phase etc.) - via the platform Prometheus
"""
from __future__ import annotations

import platform

from prometheus_client import Counter, Gauge, Histogram, Info

from .config import settings

BUILD_INFO = Gauge(
    "beacon_build_info",
    "Always 1; labels carry the running build.",
    ["version", "commit", "python_version"],
)
BUILD_INFO.labels(
    version=settings.version,
    commit=settings.git_commit,
    python_version=platform.python_version(),
).set(1)

START_TIME = Gauge("beacon_start_time_seconds", "Unix time the process started.")

HEALTH = Gauge(
    "beacon_health_status",
    "1 if the component is healthy, 0 otherwise.",
    ["component"],
)
UP = Gauge("beacon_up", "1 if every dependency is healthy (pod is ready).")

HTTP_REQUESTS = Counter(
    "beacon_http_requests_total",
    "HTTP requests handled.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "beacon_http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
HTTP_IN_FLIGHT = Gauge("beacon_http_requests_in_flight", "Requests being served.")

ERRORS = Counter(
    "beacon_errors_total",
    "Application errors by type (also logged at ERROR level).",
    ["type"],
)

VAULT_LOGINS = Counter(
    "beacon_vault_logins_total", "Vault Kubernetes-auth logins.", ["result"]
)
VAULT_SECRET_READS = Counter(
    "beacon_vault_secret_reads_total", "Vault KV reads.", ["result"]
)
VAULT_SECRET_VERSION = Gauge(
    "beacon_vault_secret_version", "KV v2 version of the secret currently in use."
)
VAULT_LAST_SUCCESS = Gauge(
    "beacon_vault_last_success_timestamp_seconds",
    "Unix time of the last successful secret read.",
)
VAULT_TOKEN_TTL = Gauge(
    "beacon_vault_token_ttl_seconds", "Remaining TTL of the Vault token."
)

DB_QUERY_LATENCY = Histogram(
    "beacon_db_query_duration_seconds",
    "Database query latency.",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
DB_POOL_SIZE = Gauge("beacon_db_pool_connections", "Connections in the pool.", ["state"])
SCHEMA_VERSION = Gauge(
    "beacon_db_schema_version", "Highest applied migration version."
)
ITEMS = Gauge("beacon_items", "Rows in beacon.items (sample business metric).")

APP_INFO = Info("beacon_app", "Static application metadata.")
APP_INFO.info({"namespace": settings.namespace, "vault_role": settings.vault_role})
