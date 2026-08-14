import sys
import unittest
from unittest import mock

from asteri.workers.tornado import TornadoDashboardMiddleware, TornadoWorker


class FakeWorker:
    def __init__(self):
        self.ppid = 999
        self.metrics_active_connections = 0
        self.stash = None
        self.calls = []
        self.shared = []

    def increment_request_metric(self, method, proto, status):
        self.calls.append(("metric", method, proto, status))

    def increment_shared_counter(self, name, delta):
        self.shared.append((name, delta))

    def generate_prometheus_metrics(self):
        return "metric_name 1\n"


class FakeApp:
    def __init__(self, result=b"app-body", exc=None, status="200 OK"):
        self.result = result
        self.exc = exc
        self.status = status
        self.calls = []

    def __call__(self, environ, start_response):
        self.calls.append((environ, start_response))
        if self.exc:
            raise self.exc
        start_response(self.status, [("Content-Type", "text/plain")])
        return [self.result]


class TestTornadoDashboardMiddleware(unittest.TestCase):
    def _make(self, app, disable_dashboard=False):
        worker = FakeWorker()
        mw = TornadoDashboardMiddleware(app, disable_dashboard, worker)
        return mw, worker

    def _sr(self):
        holder = {}

        def start_response(status, headers, exc_info=None):
            holder["status"] = status
            holder["headers"] = headers
        return start_response, holder

    def test_dashboard_endpoint(self):
        app = FakeApp()
        mw, worker = self._make(app)
        sr, holder = self._sr()
        body = mw({"PATH_INFO": "/asteri-status", "REQUEST_METHOD": "GET"}, sr)
        self.assertEqual(holder["status"], "200 OK")
        self.assertEqual(body[0][:15], b"<!DOCTYPE html>")
        self.assertFalse(app.calls)

    def test_dashboard_disabled_passthrough(self):
        app = FakeApp()
        mw, worker = self._make(app, disable_dashboard=True)
        sr, holder = self._sr()
        body = mw({"PATH_INFO": "/asteri-status", "REQUEST_METHOD": "GET"}, sr)
        self.assertEqual(body, [b"app-body"])
        self.assertEqual(holder["status"], "200 OK")
        self.assertTrue(app.calls)

    def test_dashboard_gtornado_detection(self):
        app = FakeApp()
        mw, worker = self._make(app)
        sr, holder = self._sr()
        captured = {}

        def fake_build(worker_type, pid, ppid):
            captured["worker_type"] = worker_type
            return "<html>"

        class FakeColors:
            GREEN = ""
            ENDC = ""

        with mock.patch.object(sys, "argv", ["gunicorn", "gtornado"]):
            with mock.patch("asteri.utils.build_status_html",
                            side_effect=fake_build):
                with mock.patch("asteri.utils.Colors", FakeColors):
                    body = mw({"PATH_INFO": "/asteri-status",
                               "REQUEST_METHOD": "GET"}, sr)
        self.assertEqual(body, [b"<html>"])
        self.assertEqual(captured["worker_type"],
                         "TornadoWorker (GTornado)")

    def test_dashboard_non_get_passthrough(self):
        app = FakeApp()
        mw, worker = self._make(app)
        sr, holder = self._sr()
        body = mw({"PATH_INFO": "/asteri-status", "REQUEST_METHOD": "POST"}, sr)
        self.assertEqual(body, [b"app-body"])
        self.assertTrue(app.calls)

    def test_metrics_endpoint(self):
        app = FakeApp()
        mw, worker = self._make(app)
        sr, holder = self._sr()
        body = mw({"PATH_INFO": "/metrics", "REQUEST_METHOD": "GET"}, sr)
        self.assertEqual(body, [b"metric_name 1\n"])
        self.assertEqual(holder["headers"][0],
                         ("Content-Type", "text/plain; version=0.0.4; charset=utf-8"))
        self.assertFalse(app.calls)

    def test_normal_request_metrics_and_connections(self):
        app = FakeApp()
        mw, worker = self._make(app)
        sr, holder = self._sr()
        environ = {"PATH_INFO": "/hello", "REQUEST_METHOD": "GET"}
        body = mw(environ, sr)
        self.assertEqual(body, [b"app-body"])
        self.assertEqual(worker.metrics_active_connections, 0)
        self.assertEqual(worker.calls, [("metric", "GET", "HTTP/1.1", 200)])
        self.assertTrue(app.calls)

    def test_non_numeric_status(self):
        app = FakeApp(status="abc")
        mw, worker = self._make(app)
        sr, holder = self._sr()
        mw({"PATH_INFO": "/x", "REQUEST_METHOD": "GET"}, sr)
        self.assertEqual(worker.calls, [])  # int() failed -> no metric

    def test_app_raises_500_metric(self):
        app = FakeApp(exc=RuntimeError("boom"))
        mw, worker = self._make(app)
        sr, holder = self._sr()
        with self.assertRaises(RuntimeError):
            mw({"PATH_INFO": "/x", "REQUEST_METHOD": "POST"}, sr)
        self.assertEqual(worker.calls, [("metric", "POST", "HTTP/1.1", 500)])
        self.assertEqual(worker.metrics_active_connections, 0)

    def test_app_raises_500_metric_error_swallowed(self):
        app = FakeApp(exc=RuntimeError("boom"))
        worker = FakeWorker()

        def bad_increment(method, proto, status):
            raise OSError("no stats")

        worker.increment_request_metric = bad_increment
        mw = TornadoDashboardMiddleware(app, False, worker)
        sr, holder = self._sr()
        with self.assertRaises(RuntimeError):
            mw({"PATH_INFO": "/x", "REQUEST_METHOD": "POST"}, sr)

    def test_stash_shared_counters(self):
        app = FakeApp()
        worker = FakeWorker()
        worker.stash = object()
        mw = TornadoDashboardMiddleware(app, False, worker)
        sr, holder = self._sr()
        mw({"PATH_INFO": "/s", "REQUEST_METHOD": "GET"}, sr)
        self.assertEqual(
            worker.shared,
            [("metrics.active_connections", 1), ("metrics.active_connections", -1)])

    def test_tornado_not_installed_exits(self):
        with mock.patch("asteri.workers.tornado.TORNADO_AVAILABLE", False):
            w = TornadoWorker(0, 999, [], "example_wsgi:app", 30)
            with mock.patch.object(w, "app", "app"):
                with self.assertRaises(SystemExit):
                    w.run()

    def _run_worker(self, getppid_value, alive):
        w = TornadoWorker(0, 999, [], "example_wsgi:app", 30)
        with mock.patch.object(w, "app", mock.Mock()):
            with mock.patch("asteri.workers.tornado.tornado") as tornado_mock:
                server_mock = mock.Mock()
                tornado_mock.httpserver.HTTPServer.return_value = server_mock
                loop_mock = mock.Mock()
                tornado_mock.ioloop.IOLoop.current.return_value = loop_mock
                pc_mock = mock.Mock()
                tornado_mock.ioloop.PeriodicCallback.return_value = pc_mock
                with mock.patch("asteri.workers.tornado.os.getppid",
                                return_value=getppid_value):
                    with mock.patch.object(w, "alive", alive):
                        w.run()
        callback = tornado_mock.ioloop.PeriodicCallback.call_args_list[0].args[0]
        return callback, server_mock, loop_mock

    def test_check_parent_stops_on_ppid_change(self):
        callback, server_mock, loop_mock = self._run_worker(-1, True)
        callback()
        server_mock.stop.assert_called()
        loop_mock.stop.assert_called()

    def test_check_parent_stops_when_not_alive(self):
        callback, server_mock, loop_mock = self._run_worker(999, False)
        callback()
        server_mock.stop.assert_called()
        loop_mock.stop.assert_called()

    def test_run_loop_start_keyboard_interrupt(self):
        w = TornadoWorker(0, 999, [], "example_wsgi:app", 30)
        with mock.patch.object(w, "app", mock.Mock()):
            with mock.patch("asteri.workers.tornado.tornado") as tornado_mock:
                server_mock = mock.Mock()
                tornado_mock.httpserver.HTTPServer.return_value = server_mock
                loop_mock = mock.Mock()
                loop_mock.start.side_effect = KeyboardInterrupt()
                tornado_mock.ioloop.IOLoop.current.return_value = loop_mock
                with mock.patch("asteri.workers.tornado.os.getppid",
                                return_value=999):
                    with mock.patch.object(w, "alive", True):
                        w.run()
        server_mock.stop.assert_called()


if __name__ == "__main__":
    unittest.main()