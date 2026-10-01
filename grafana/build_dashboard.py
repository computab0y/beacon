#!/usr/bin/env python3
"""Generates beacon-dashboard.json. Edit here, then: python grafana/build_dashboard.py

Datasources are template variables, so the dashboard works with whatever your
Prometheus/Thanos and Loki datasources are called.
"""
import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "${ds_prom}"}
LOKI = {"type": "loki", "uid": "${ds_loki}"}
NS = 'namespace="$namespace"'
APP = f'{NS},job="beacon"'
# Matches "level": "ERROR" both in raw JSON lines (Promtail/Alloy) and in the
# escaped "message" field written by OpenShift Logging / Vector.
ERR_RE = r'level\\?"\s*:\s*\\?"(ERROR|CRITICAL)'
STREAM = '{${ns_label}="$namespace"}'

_id = 0


def nid():
    global _id
    _id += 1
    return _id


def prom(expr, legend="", instant=False, fmt="time_series", ref="A"):
    t = {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref,
         "format": fmt}
    if instant:
        t.update(instant=True, range=False)
    return t


def updown(text_up="UP", text_down="DOWN"):
    return [{"type": "value", "options": {
        "1": {"text": text_up, "color": "green", "index": 0},
        "0": {"text": text_down, "color": "red", "index": 1}}},
        {"type": "special", "options": {"match": "null", "result": {
            "text": "NO DATA", "color": "orange", "index": 2}}}]


def stat(title, targets, x, y, w=3, h=4, unit="none", mappings=None, thresholds=None,
         text_mode="auto", color_mode="background", desc="", decimals=None, ds=PROM):
    fd = {"unit": unit, "mappings": mappings or [],
          "thresholds": {"mode": "absolute", "steps": thresholds or [
              {"color": "blue", "value": None}]},
          "color": {"mode": "thresholds"}}
    if decimals is not None:
        fd["decimals"] = decimals
    return {"id": nid(), "type": "stat", "title": title, "description": desc,
            "datasource": ds, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": targets,
            "fieldConfig": {"defaults": fd, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "",
                                          "values": False},
                        "colorMode": color_mode, "graphMode": "none",
                        "textMode": text_mode, "justifyMode": "center",
                        "orientation": "auto", "wideLayout": True}}


def ts(title, targets, x, y, w=12, h=8, unit="short", stack=False, bars=False,
       desc="", ds=PROM, min0=True, legend_calcs=None):
    custom = {"drawStyle": "bars" if bars else "line", "lineWidth": 1,
              "fillOpacity": 60 if bars else 10, "showPoints": "never",
              "spanNulls": True,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if min0:
        defaults["min"] = 0
    return {"id": nid(), "type": "timeseries", "title": title, "description": desc,
            "datasource": ds, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": targets,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "table" if legend_calcs else "list",
                                   "placement": "bottom", "calcs": legend_calcs or []},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def row(title, y):
    return {"id": nid(), "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def logs(title, expr, x, y, w=24, h=10, desc=""):
    return {"id": nid(), "type": "logs", "title": title, "description": desc,
            "datasource": LOKI, "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "targets": [{"datasource": LOKI, "expr": expr, "refId": "A",
                         "queryType": "range"}],
            "options": {"showTime": True, "wrapLogMessage": True,
                        "prettifyLogMessage": False, "enableLogDetails": True,
                        "sortOrder": "Descending", "dedupStrategy": "none"}}


GREEN_RED = [{"color": "green", "value": None}, {"color": "red", "value": 1}]
panels = []
y = 0

# ------------------------------------------------------------------ overview
panels.append(row("Overview", y))
y += 1
panels += [
    stat("Version(s) running",
         [prom(f"count by (version) (beacon_build_info{{{APP}}})", "v{{version}}",
               instant=True)],
         0, y, w=6, text_mode="name", color_mode="none",
         desc="One entry per distinct version currently scraped. Two entries = rollout "
              "in progress (or stuck)."),
    stat("Pods running", [prom(f"count(beacon_build_info{{{APP}}})", instant=True)],
         6, y, desc="Pods being scraped (process up and serving /metrics)."),
    stat("Pods ready", [prom(f"sum(beacon_up{{{APP}}})", instant=True)], 9, y,
         thresholds=[{"color": "red", "value": None}, {"color": "green", "value": 1}],
         desc="Pods whose Vault + database checks pass."),
    stat("Desired replicas",
         [prom(f'max(kube_deployment_spec_replicas{{{NS},deployment="beacon"}})',
               instant=True)], 12, y,
         desc="From kube-state-metrics (needs the Thanos querier datasource)."),
    stat("Application health", [prom(f"min(beacon_up{{{APP}}})", instant=True)], 15, y,
         mappings=updown("HEALTHY", "DEGRADED"),
         desc="HEALTHY when every pod reports all dependencies up."),
    stat("Vault", [prom(f'min(beacon_health_status{{{APP},component="vault"}})',
                        instant=True)], 18, y, mappings=updown()),
    stat("Database (PG4K)",
         [prom(f'min(beacon_health_status{{{APP},component="database"}})', instant=True)],
         21, y, mappings=updown()),
]
y += 4
panels += [
    stat("Uptime (newest pod)",
         [prom(f"time() - max(beacon_start_time_seconds{{{APP}}})", instant=True)],
         0, y, w=4, unit="s", color_mode="none"),
    stat("DB schema version", [prom(f"max(beacon_db_schema_version{{{APP}}})",
                                    instant=True)], 4, y, w=4, color_mode="none"),
    stat("Vault secret version", [prom(f"max(beacon_vault_secret_version{{{APP}}})",
                                       instant=True)], 8, y, w=4, color_mode="none"),
    stat("App errors (range)",
         [prom(f"sum(increase(beacon_errors_total{{{APP}}}[$__range]))", instant=True)],
         12, y, w=4, thresholds=GREEN_RED, decimals=0,
         desc="beacon_errors_total over the selected time range."),
    stat("ERROR log lines (range)",
         [{"datasource": LOKI, "refId": "A", "queryType": "instant",
           "expr": f"sum(count_over_time({STREAM} |~ `{ERR_RE}` [$__range]))"}],
         16, y, w=4, thresholds=GREEN_RED, ds=LOKI,
         desc="Counted from Loki - ERROR/CRITICAL lines from any beacon pod."),
    stat("5xx ratio (5m)",
         [prom(f'sum(rate(beacon_http_requests_total{{{APP},status=~"5..",'
               f'route!~"/healthz.*"}}[5m])) / sum(rate(beacon_http_requests_total'
               f'{{{APP},route!~"/healthz.*"}}[5m]))', instant=True)],
         20, y, w=4, unit="percentunit",
         thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.01},
                     {"color": "red", "value": 0.05}]),
]
y += 4

# ------------------------------------------------------------------ pods
panels.append(row("Pods", y))
y += 1
pod_table = {
    "id": nid(), "type": "table", "title": "Pod status",
    "description": "Version/commit from beacon_build_info, readiness from beacon_up, "
                   "phase and restarts from kube-state-metrics.",
    "datasource": PROM, "gridPos": {"x": 0, "y": y, "w": 24, "h": 7},
    "targets": [
        prom(f"max by (pod, version, commit) (beacon_build_info{{{APP}}})",
             fmt="table", instant=True, ref="A"),
        prom(f"max by (pod) (beacon_up{{{APP}}})", fmt="table", instant=True, ref="B"),
        prom(f'max by (pod, phase) (kube_pod_status_phase{{{NS},pod=~"beacon-.*"}} == 1)',
             fmt="table", instant=True, ref="C"),
        prom(f'sum by (pod) (kube_pod_container_status_restarts_total{{{NS},'
             f'container="beacon"}})', fmt="table", instant=True, ref="D"),
        prom(f"time() - max by (pod) (beacon_start_time_seconds{{{APP}}})", fmt="table",
             instant=True, ref="E"),
    ],
    "transformations": [
        {"id": "joinByField", "options": {"byField": "pod", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "Time 3": True,
                              "Time 4": True, "Time 5": True, "Value #A": True,
                              "Value #C": True},
            "indexByName": {"pod": 0, "phase": 1, "Value #B": 2, "version": 3,
                            "commit": 4, "Value #D": 5, "Value #E": 6},
            "renameByName": {"pod": "Pod", "phase": "Phase", "Value #B": "Ready",
                             "version": "Version", "commit": "Commit",
                             "Value #D": "Restarts", "Value #E": "Uptime"}}},
    ],
    "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                    "overrides": [
        {"matcher": {"id": "byName", "options": "Ready"}, "properties": [
            {"id": "mappings", "value": updown("Yes", "No")},
            {"id": "custom.cellOptions", "value": {"type": "color-background"}}]},
        {"matcher": {"id": "byName", "options": "Phase"}, "properties": [
            {"id": "mappings", "value": [{"type": "value", "options": {
                "Running": {"color": "green", "index": 0},
                "Succeeded": {"color": "blue", "index": 1},
                "Pending": {"color": "orange", "index": 2},
                "Failed": {"color": "red", "index": 3},
                "Unknown": {"color": "red", "index": 4}}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}}]},
        {"matcher": {"id": "byName", "options": "Restarts"}, "properties": [
            {"id": "thresholds", "value": {"mode": "absolute", "steps": [
                {"color": "green", "value": None}, {"color": "orange", "value": 1},
                {"color": "red", "value": 5}]}},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}}]},
        {"matcher": {"id": "byName", "options": "Uptime"}, "properties": [
            {"id": "unit", "value": "s"}]},
        {"matcher": {"id": "byName", "options": "Commit"}, "properties": [
            {"id": "custom.width", "value": 340}]},
    ]},
    "options": {"showHeader": True, "cellHeight": "sm",
                "sortBy": [{"displayName": "Pod", "desc": False}]},
}
panels.append(pod_table)
y += 7
panels += [
    ts("Running pods by version",
       [prom(f"count by (version) (beacon_build_info{{{APP}}})", "v{{version}}")],
       0, y, stack=True, desc="Rollouts show up as one version handing over to the next."),
    ts("Pods by phase",
       [prom(f'sum by (phase) (kube_pod_status_phase{{{NS},pod=~"beacon-.*"}} == 1)',
             "{{phase}}")], 12, y, stack=True),
]
y += 8
panels += [
    ts("Dependency health by component",
       [prom(f"min by (component) (beacon_health_status{{{APP}}})", "{{component}}")],
       0, y, desc="1 = healthy on every pod, 0 = at least one pod failing."),
    ts("Container restarts",
       [prom(f'sum by (pod) (increase(kube_pod_container_status_restarts_total{{{NS},'
             f'container="beacon"}}[$__rate_interval]))', "{{pod}}")], 12, y, bars=True),
]
y += 8

# ------------------------------------------------------------------ traffic
panels.append(row("Traffic & errors", y))
y += 1
panels += [
    ts("Requests / s by route",
       [prom(f'sum by (route) (rate(beacon_http_requests_total{{{APP},'
             f'route!~"/healthz.*|/metrics"}}[$__rate_interval]))', "{{route}}")],
       0, y, unit="reqps"),
    ts("Requests / s by status",
       [prom(f'sum by (status) (rate(beacon_http_requests_total{{{APP},'
             f'route!~"/healthz.*|/metrics"}}[$__rate_interval]))', "{{status}}")],
       12, y, unit="reqps", stack=True),
]
y += 8
lat = []
for i, q in enumerate(("0.5", "0.95", "0.99")):
    lat.append(prom(
        f'histogram_quantile({q}, sum by (le) (rate(beacon_http_request_duration_seconds_bucket'
        f'{{{APP},route!~"/healthz.*|/metrics"}}[$__rate_interval])))',
        f"p{int(float(q) * 100)}", ref="ABC"[i]))
panels += [
    ts("Latency", lat, 0, y, unit="s"),
    ts("Application errors by type",
       [prom(f"sum by (type) (increase(beacon_errors_total{{{APP}}}[$__rate_interval]))",
             "{{type}}")], 12, y, bars=True, stack=True,
       desc="Every increment here is also logged at ERROR level - see Logs below."),
]
y += 8

# ------------------------------------------------------------------ deps/resources
panels.append(row("Resources & dependencies", y))
y += 1
panels += [
    ts("Memory (RSS) by pod",
       [prom(f"process_resident_memory_bytes{{{APP}}}", "{{pod}}")], 0, y, w=8,
       unit="bytes"),
    ts("CPU by pod",
       [prom(f"rate(process_cpu_seconds_total{{{APP}}}[$__rate_interval])", "{{pod}}")],
       8, y, w=8, unit="cores"),
    ts("DB query p95 by operation",
       [prom(f'histogram_quantile(0.95, sum by (le, operation) (rate('
             f'beacon_db_query_duration_seconds_bucket{{{APP}}}[$__rate_interval])))',
             "{{operation}}")], 16, y, w=8, unit="s"),
]
y += 8
panels += [
    ts("Vault secret age by pod",
       [prom(f"time() - beacon_vault_last_success_timestamp_seconds{{{APP}}}", "{{pod}}")],
       0, y, w=8, unit="s", desc="Time since each pod last read its secret from Vault."),
    ts("Vault logins / reads",
       [prom(f"sum by (result) (increase(beacon_vault_logins_total{{{APP}}}"
             f"[$__rate_interval]))", "login {{result}}", ref="A"),
        prom(f"sum by (result) (increase(beacon_vault_secret_reads_total{{{APP}}}"
             f"[$__rate_interval]))", "read {{result}}", ref="B")],
       8, y, w=8, bars=True),
    ts("Items in DB / pool connections",
       [prom(f"max(beacon_items{{{APP}}})", "items", ref="A"),
        prom(f"sum by (state) (beacon_db_pool_connections{{{APP}}})", "pool {{state}}",
             ref="B")], 16, y, w=8),
]
y += 8

# ------------------------------------------------------------------ logs
panels.append(row("Logs (Loki)", y))
y += 1
panels.append(ts(
    "ERROR log lines",
    [{"datasource": LOKI, "refId": "A", "queryType": "range",
      "expr": f"sum(count_over_time({STREAM} |~ `{ERR_RE}` [$__interval]))",
      "legendFormat": "errors"}],
    0, y, w=24, h=6, bars=True, ds=LOKI))
y += 6
panels.append(logs("Errors", f"{STREAM} |~ `{ERR_RE}`", 0, y, h=10,
                   desc="Expand a line for the stack trace, request_id, pod and version."))
y += 10
panels.append(logs("All logs (filter with the 'search' box)",
                   f'{STREAM} != "/healthz" |= "$search"', 0, y, h=12))
y += 12

dashboard = {
    "uid": "beacon-overview",
    "title": "Beacon - application overview",
    "tags": ["beacon", "okd"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "editable": True,
    "graphTooltip": 1,
    "refresh": "30s",
    "time": {"from": "now-3h", "to": "now"},
    "templating": {"list": [
        {"name": "ds_prom", "label": "Prometheus", "type": "datasource",
         "query": "prometheus", "current": {}, "hide": 0, "refresh": 1},
        {"name": "ds_loki", "label": "Loki", "type": "datasource", "query": "loki",
         "current": {}, "hide": 0, "refresh": 1},
        {"name": "namespace", "label": "Namespace", "type": "query", "datasource": PROM,
         "query": {"query": "label_values(beacon_build_info, namespace)", "refId": "ns"},
         "definition": "label_values(beacon_build_info, namespace)",
         "current": {"text": "beacon", "value": "beacon"}, "refresh": 2, "hide": 0},
        {"name": "ns_label", "label": "Loki namespace label", "type": "custom",
         "description": "kubernetes_namespace_name = OpenShift Logging (Vector/LokiStack);"
                        " namespace = Promtail / Grafana Alloy",
         "query": "kubernetes_namespace_name,namespace",
         "current": {"text": "kubernetes_namespace_name",
                     "value": "kubernetes_namespace_name"},
         "options": [], "hide": 0},
        {"name": "search", "label": "Log search", "type": "textbox", "query": "",
         "current": {"text": "", "value": ""}, "hide": 0},
    ]},
    "annotations": {"list": [
        {"name": "Annotations & Alerts", "builtIn": 1, "enable": True, "hide": True,
         "type": "dashboard", "iconColor": "rgba(0, 211, 255, 1)",
         "datasource": {"type": "grafana", "uid": "-- Grafana --"}},
        {"name": "Pod starts", "enable": True, "iconColor": "purple",
         "datasource": LOKI,
         "expr": STREAM + r' |~ `msg\\?"\s*:\s*\\?"starting\\?"`',
         "titleFormat": "beacon pod started", "textFormat": "{{__line__}}"},
    ]},
    "panels": panels,
}

out = Path(__file__).with_name("beacon-dashboard.json")
out.write_text(json.dumps(dashboard, indent=2) + "\n")
print(f"wrote {out} ({len(panels)} panels)")
