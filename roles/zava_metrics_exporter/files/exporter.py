from __future__ import annotations

import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable

import pymssql
from prometheus_client import (
    CollectorRegistry,
    CounterMetricFamily,
    GaugeMetricFamily,
    generate_latest,
)


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


class Database:
    def connect(self) -> pymssql.Connection:
        return pymssql.connect(
            server=env("SHIPMENT_DB_HOST", "192.168.90.11"),
            port=int(env("SHIPMENT_DB_PORT", "1433")),
            user=env("SHIPMENT_DB_USER", "shipping_app"),
            password=env("SHIPMENT_DB_PASSWORD"),
            database=env("SHIPMENT_DB_NAME", "shipping"),
            login_timeout=int(env("SHIPMENT_DB_LOGIN_TIMEOUT", "5")),
            timeout=int(env("SHIPMENT_DB_QUERY_TIMEOUT", "10")),
            as_dict=True,
        )

    def query(self, sql: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                return list(cursor.fetchall())


class ZavaCollector:
    def __init__(self) -> None:
        self.database = Database()
        self.failures = 0

    def collect(self) -> Iterable[Any]:
        started = time.monotonic()
        up = GaugeMetricFamily("zava_exporter_up", "Whether the exporter reached SQL Server")
        duration = GaugeMetricFamily(
            "zava_exporter_collection_duration_seconds",
            "Time spent collecting one Zava metric snapshot",
        )
        failures = CounterMetricFamily(
            "zava_exporter_collection_failures",
            "SQL collection failures observed by the exporter",
        )

        try:
            metrics = list(self._collect_business_metrics())
            up.add_metric([], 1)
            yield up
            yield from metrics
        except Exception:
            self.failures += 1
            up.add_metric([], 0)
            yield up
        finally:
            duration.add_metric([], time.monotonic() - started)
            failures.add_metric([], self.failures)
            yield duration
            yield failures

    def _collect_business_metrics(self) -> Iterable[Any]:
        shipment_rows = self.database.query(
            "SELECT status, COUNT_BIG(*) AS value FROM dbo.shipments GROUP BY status"
        )
        total_shipments = GaugeMetricFamily(
            "zava_shipments", "Current shipment count", labels=["status"]
        )
        for row in shipment_rows:
            total_shipments.add_metric([row["status"]], float(row["value"]))
        yield total_shipments

        queue = self.database.query(
            """
            SELECT
              COUNT_BIG(*) AS arrivals,
              SUM(CASE WHEN processed_at IS NOT NULL THEN 1 ELSE 0 END) AS completions,
              SUM(CASE WHEN processed_at IS NULL THEN 1 ELSE 0 END) AS depth,
              COALESCE(DATEDIFF(second,
                MIN(CASE WHEN processed_at IS NULL THEN enqueued_at END),
                SYSUTCDATETIME()), 0) AS oldest_age,
              SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
              SUM(CASE WHEN attempts > 1 THEN attempts - 1 ELSE 0 END) AS retries,
              SUM(CASE WHEN processed_at IS NULL
                        AND enqueued_at < DATEADD(second,
                          -%d, SYSUTCDATETIME())
                       THEN 1 ELSE 0 END) AS stuck
            FROM dbo.scan_queue
            """
            % int(env("ZAVA_STUCK_WORK_THRESHOLD_SECONDS", "30"))
        )[0]

        for name, help_text, key in (
            ("zava_queue_depth", "Unprocessed shipment work items", "depth"),
            (
                "zava_queue_oldest_age_seconds",
                "Age of the oldest unprocessed work item",
                "oldest_age",
            ),
            (
                "zava_queue_items_with_error",
                "Queue items retaining a processing error",
                "errors",
            ),
            (
                "zava_queue_stuck_items",
                "Unprocessed work older than the configured threshold",
                "stuck",
            ),
        ):
            metric = GaugeMetricFamily(name, help_text)
            metric.add_metric([], float(queue[key] or 0))
            yield metric

        arrivals = CounterMetricFamily(
            "zava_queue_arrivals", "Shipment work items enqueued"
        )
        arrivals.add_metric([], float(queue["arrivals"] or 0))
        yield arrivals

        completions = CounterMetricFamily(
            "zava_queue_completions", "Shipment work items processed"
        )
        completions.add_metric([], float(queue["completions"] or 0))
        yield completions

        retries = CounterMetricFamily(
            "zava_queue_retries", "Queue attempts beyond the first attempt"
        )
        retries.add_metric([], float(queue["retries"] or 0))
        yield retries

        latency_rows = self.database.query(
            """
            SELECT TOP (1)
              PERCENTILE_CONT(0.50) WITHIN GROUP (
                ORDER BY DATEDIFF_BIG(millisecond, enqueued_at, processed_at) / 1000.0
              ) OVER () AS p50,
              PERCENTILE_CONT(0.95) WITHIN GROUP (
                ORDER BY DATEDIFF_BIG(millisecond, enqueued_at, processed_at) / 1000.0
              ) OVER () AS p95,
              PERCENTILE_CONT(0.99) WITHIN GROUP (
                ORDER BY DATEDIFF_BIG(millisecond, enqueued_at, processed_at) / 1000.0
              ) OVER () AS p99
            FROM dbo.scan_queue
            WHERE processed_at IS NOT NULL
              AND processed_at >= DATEADD(hour, -1, SYSUTCDATETIME())
            """
        )
        quantiles = GaugeMetricFamily(
            "zava_queue_latency_quantile_seconds",
            "Queue end-to-end latency quantiles over the last hour",
            labels=["quantile"],
        )
        if latency_rows:
            for key, label in (("p50", "0.50"), ("p95", "0.95"), ("p99", "0.99")):
                if latency_rows[0][key] is not None:
                    quantiles.add_metric([label], float(latency_rows[0][key]))
        yield quantiles

        status_age = GaugeMetricFamily(
            "zava_status_oldest_age_seconds",
            "Age of the oldest shipment update in each current status",
            labels=["status"],
        )
        for row in self.database.query(
            """
            SELECT status,
                   DATEDIFF(second, MIN(updated_at), SYSUTCDATETIME()) AS age_seconds
            FROM dbo.shipments
            WHERE status <> 'DELIVERED'
            GROUP BY status
            """
        ):
            status_age.add_metric([row["status"]], float(row["age_seconds"] or 0))
        yield status_age

        scans = CounterMetricFamily(
            "zava_scan_events",
            "Shipment scan events produced by workers",
            labels=["worker_host", "worker_site", "scan_type"],
        )
        for row in self.database.query(
            """
            SELECT worker_host, worker_site, scan_type, COUNT_BIG(*) AS value
            FROM dbo.scan_events
            GROUP BY worker_host, worker_site, scan_type
            """
        ):
            scans.add_metric(
                [row["worker_host"], row["worker_site"], row["scan_type"]],
                float(row["value"]),
            )
        yield scans

        transitions = CounterMetricFamily(
            "zava_status_transitions",
            "Shipment status transitions",
            labels=["to_status"],
        )
        for row in self.database.query(
            """
            SELECT to_status, COUNT_BIG(*) AS value
            FROM dbo.status_history
            GROUP BY to_status
            """
        ):
            transitions.add_metric([row["to_status"]], float(row["value"]))
        yield transitions

        heartbeat = GaugeMetricFamily(
            "zava_tier_heartbeat_age_seconds",
            "Seconds since each application tier reported",
            labels=["tier", "hostname", "distro", "platform", "location"],
        )
        for row in self.database.query(
            """
            SELECT tier, hostname, distro, platform, location,
                   DATEDIFF(second, last_seen, SYSUTCDATETIME()) AS age_seconds
            FROM dbo.tier_heartbeats
            """
        ):
            heartbeat.add_metric(
                [
                    row["tier"],
                    row["hostname"],
                    row["distro"],
                    row["platform"],
                    row["location"],
                ],
                float(row["age_seconds"] or 0),
            )
        yield heartbeat


collector = ZavaCollector()
registry = CollectorRegistry(auto_describe=True)
registry.register(collector)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return

        content = generate_latest(registry)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    port = int(env("ZAVA_EXPORTER_PORT", "9108"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
