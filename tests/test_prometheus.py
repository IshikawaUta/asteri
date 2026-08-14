import unittest
import time
import os
from asteri.workers.base import BaseWorker
from asteri.dirty import StashServer, StashClient


class DummyWorker(BaseWorker):
    def run(self):
        pass


class TestPrometheusIntegration(unittest.TestCase):
    def setUp(self):
        # Create a dummy worker with disabled control socket and mock sockets
        self.worker = DummyWorker(
            age=0,
            ppid=os.getpid(),
            sockets=[],
            app_path="test_app",
            timeout=30,
            disable_dashboard=True,
        )

    def test_prometheus_increment_logic(self):
        # Verify increment_request_metric correctly increments local store
        self.worker.increment_request_metric("GET", "HTTP/1.1", 200)
        self.worker.increment_request_metric("GET", "HTTP/1.1", 200)
        self.worker.increment_request_metric("POST", "HTTP/2", 404)

        self.assertEqual(
            self.worker.metrics_requests_total[("GET", "HTTP/1.1", "2xx")], 2
        )
        self.assertEqual(
            self.worker.metrics_requests_total[("POST", "HTTP/2", "4xx")], 1
        )

    def test_prometheus_exposition_format(self):
        # Populate worker metrics
        self.worker.increment_request_metric("GET", "HTTP/1.1", 200)
        self.worker.increment_request_metric("POST", "HTTP/2", 500)
        self.worker.metrics_active_connections = 4

        # Generate metrics text
        metrics_text = self.worker.generate_prometheus_metrics()

        # Assert Prometheus Exposition header declarations
        self.assertIn(
            "# HELP asteri_workers_count Number of active workers", metrics_text
        )
        self.assertIn("# TYPE asteri_workers_count gauge", metrics_text)
        self.assertIn(
            "# HELP asteri_requests_total Total number of HTTP requests processed",
            metrics_text,
        )
        self.assertIn("# TYPE asteri_requests_total counter", metrics_text)

        # Assert exact formatted metrics labels
        self.assertIn(
            'asteri_requests_total{method="GET",protocol="HTTP/1.1",status_class="2xx"} 1',
            metrics_text,
        )
        self.assertIn(
            'asteri_requests_total{method="POST",protocol="HTTP/2",status_class="5xx"} 1',
            metrics_text,
        )
        self.assertIn("asteri_active_connections 4", metrics_text)

        # Assert OpenTelemetry Semantic Conventions
        self.assertIn(
            "# HELP http_server_active_requests Number of active HTTP requests",
            metrics_text,
        )
        self.assertIn("http_server_active_requests 4", metrics_text)
        self.assertIn(
            'http_server_duration_milliseconds_count{http_method="GET",http_status_code="2xx",http_flavor="1.1"} 1',
            metrics_text,
        )
        self.assertIn(
            'http_server_duration_milliseconds_count{http_method="POST",http_status_code="5xx",http_flavor="2.0"} 1',
            metrics_text,
        )

    def test_prometheus_stash_aggregation(self):
        # Setup a temporary IPC address for Stash server
        stash_addr = "/tmp/asteri_test_prometheus_stash.sock"
        if os.path.exists(stash_addr):
            try:
                os.unlink(stash_addr)
            except OSError:
                pass

        # Start a local StashServer
        server = StashServer(stash_addr)
        server.start()
        time.sleep(0.1)  # allow server to bind

        try:
            # Connect worker's Stash client to our server
            self.worker.stash = StashClient(stash_addr)

            # Record some metrics that will get written to the Stash server IPC store
            self.worker.increment_request_metric("GET", "HTTP/1.1", 200)
            self.worker._flush_metrics(force=True)

            # Directly verify increment occurred in the Stash server storage
            wc_val = self.worker.stash.get(
                "metrics.requests_total.GET.HTTP/1.1.2xx")
            self.assertIsNotNone(wc_val)
            self.assertEqual(int(wc_val.decode("utf-8")), 1)

            # Now set variables inside stash representing other workers' operations
            self.worker.stash.set("metrics.workers_count", b"3")
            self.worker.stash.set("metrics.active_connections", b"12")

            # Re-generate metrics and assert cluster-wide values
            metrics_text = self.worker.generate_prometheus_metrics()

            self.assertIn("asteri_workers_count 3", metrics_text)
            self.assertIn("asteri_active_connections 12", metrics_text)
            self.assertIn(
                'asteri_requests_total{method="GET",protocol="HTTP/1.1",status_class="2xx"} 1',
                metrics_text,
            )

        finally:
            server.stop()
            if os.path.exists(stash_addr):
                try:
                    os.unlink(stash_addr)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
