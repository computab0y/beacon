"""Structured JSON logging to stdout so Loki can parse it with `| json`.

Every line carries app, version, pod and (when inside a request) request_id,
so errors in Grafana can be tied back to a release and a pod.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import traceback
from datetime import UTC, datetime

from .config import settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

MAX_STACK = 4000
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "app": settings.app_name,
            "version": settings.version,
            "pod": settings.pod_name,
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        # Anything passed via `extra={...}` becomes a top-level field.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            etype, evalue, _ = record.exc_info
            payload["error_type"] = etype.__name__ if etype else None
            payload["error"] = str(evalue)
            stack = "".join(traceback.format_exception(*record.exc_info))
            # Keep the tail (where the error was raised); framework chains get long.
            payload["stack"] = stack if len(stack) <= MAX_STACK else "..." + stack[-MAX_STACK:]
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    # Route uvicorn through the same formatter; we emit our own access log.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    # Keep noisy libraries quiet.
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # Pool reconnect chatter; outages are reported once by the health loop.
    logging.getLogger("psycopg.pool").setLevel(logging.ERROR)
