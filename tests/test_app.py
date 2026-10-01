import json
import logging
import os

os.environ["BEACON_DISABLE_BACKGROUND"] = "1"
os.environ["ENABLE_CHAOS_ENDPOINTS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.logging_setup import JsonFormatter  # noqa: E402
from app.migrate import discover  # noqa: E402


def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_liveness():
    with client() as c:
        r = c.get("/healthz/live")
    assert r.status_code == 200


def test_not_ready_without_dependencies():
    with client() as c:
        r = c.get("/healthz/ready")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "vault": False, "database": False}


def test_metrics_expose_build_info_and_health():
    with client() as c:
        body = c.get("/metrics").text
    assert 'beacon_build_info{commit=' in body
    assert 'beacon_health_status{component="vault"} 0.0' in body
    assert "beacon_http_requests_total" in body


def test_simulated_error_counted_and_returns_500():
    with client() as c:
        r = c.post("/api/simulate-error")
        body = c.get("/metrics").text
    assert r.status_code == 500
    assert "request_id" in r.json()
    assert 'beacon_errors_total{type="RuntimeError"}' in body
    assert 'route="/api/simulate-error",status="500"' in body


def test_json_log_format_includes_stack():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        rec = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    rec.component = "database"
    out = json.loads(JsonFormatter().format(rec))
    assert out["level"] == "ERROR"
    assert out["error_type"] == "ValueError"
    assert out["component"] == "database"
    assert "Traceback" in out["stack"]
    assert {"ts", "version", "pod", "app"} <= out.keys()


def test_migrations_are_well_formed():
    versions = [v for v, _, _ in discover()]
    assert versions == sorted(versions) and versions[0] == 1
