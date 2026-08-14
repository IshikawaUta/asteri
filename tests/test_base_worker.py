import os
import socket
import struct
import unittest
from unittest import mock

from asteri.workers.base import BaseWorker


class FakeSock:
    """Records everything sent; serves canned recv chunks."""

    def __init__(self, recv_chunks):
        self.recv_chunks = list(recv_chunks)
        self.sent = []
        self.closed = False
        self.timeouts = []

    def recv(self, size=8192):
        if not self.recv_chunks:
            return b""
        data = self.recv_chunks[0]
        if len(data) <= size:
            self.recv_chunks.pop(0)
            return data
        self.recv_chunks[0] = data[size:]
        return data[:size]

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, t):
        self.timeouts.append(t)

    def setsockopt(self, *a):
        raise OSError("no tcp")

    def close(self):
        self.closed = True


def make_worker(**kw):
    w = BaseWorker(0, 999, [], "dummy:app", 30, **kw)
    return w


class TestHandleRequest(unittest.TestCase):
    def _run(self, chunks, **kw):
        sock = FakeSock(chunks)
        w = make_worker(**kw)
        w.handle_http = mock.Mock()
        w.handle_uwsgi = mock.Mock()
        w.handle_h2_request = mock.Mock(return_value=(200, [], b""))
        w.handle_request(sock)
        return sock, w

    def test_normal_request_close(self):
        sock, w = self._run(
            [b"GET / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n"])
        self.assertEqual(w.handle_http.call_count, 1)
        self.assertTrue(sock.closed)

    def test_keepalive_pipelined_second_request(self):
        req1 = b"GET /a HTTP/1.1\r\nHost: h\r\n\r\n"
        req2 = b"GET /b HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n"
        sock, w = self._run([req1 + req2], keep_alive=2)
        self.assertEqual(w.handle_http.call_count, 2)
        self.assertIn(2, sock.timeouts)  # keep-alive timeout applied

    def test_keepalive_no_more_data_returns(self):
        sock, w = self._run([b"GET /a HTTP/1.1\r\nHost: h\r\n\r\n", b""],
                            keep_alive=2)
        self.assertEqual(w.handle_http.call_count, 1)

    def test_http11_close_without_keepalive(self):
        sock, w = self._run([b"GET / HTTP/1.0\r\nHost: h\r\n\r\n"])
        self.assertEqual(w.handle_http.call_count, 1)
        self.assertTrue(sock.closed)

    def test_empty_recv_returns(self):
        sock, w = self._run([b""])
        self.assertEqual(w.handle_http.call_count, 0)

    def test_header_limit_exceeded_431(self):
        big = b"X" * (32768 + 100)
        sock, w = self._run([big])
        self.assertEqual(w.handle_http.call_count, 0)
        self.assertTrue(any(b"431" in s for s in sock.sent))

    def test_proxy_protocol_first_request(self):
        req = b"PROXY TCP4 10.0.0.1 10.0.0.2 555 80\r\n" + \
              b"GET / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n"
        sock, w = self._run([req], proxy_protocol=True)
        self.assertEqual(w.handle_http.call_count, 1)
        connector = w.handle_http.call_args.kwargs.get("connector")
        self.assertEqual(connector["proxy_client"], ("10.0.0.1", 555))
        self.assertEqual(connector["proxy_server"], ("10.0.0.2", 80))

    def test_dashboard_endpoint(self):
        sock, w = self._run(
            [b"GET /asteri-status HTTP/1.1\r\nHost: h\r\n\r\n"])
        out = b"".join(sock.sent)
        self.assertIn(b"text/html", out)
        self.assertEqual(w.handle_http.call_count, 0)

    def test_dashboard_endpoint_disabled(self):
        req = b"GET /asteri-status HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n"
        sock, w = self._run([req], disable_dashboard=True)
        self.assertEqual(w.handle_http.call_count, 1)

    def test_metrics_endpoint(self):
        sock, w = self._run([b"GET /metrics HTTP/1.1\r\nHost: h\r\n\r\n"])
        out = b"".join(sock.sent)
        self.assertIn(b"text/plain; version=0.0.4", out)
        self.assertEqual(w.handle_http.call_count, 0)

    def test_http2_dispatch(self):
        with mock.patch("asteri.workers.base.HTTP2Handler") as H2:
            sock, w = self._run(
                [b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\nsome-frames"])
            H2.assert_called_once()
            H2.return_value.handle.assert_called_once()
        self.assertEqual(w.handle_http.call_count, 0)

    def test_uwsgi_dispatch(self):
        def packet():
            var = b""
            for k, v in {"REQUEST_METHOD": "GET",
                         "PATH_INFO": "/",
                         "HTTP_X": "\r\n\r\n"}.items():  # CRLF block to exit read loop
                kb, vb = k.encode(), v.encode()
                var += struct.pack("<H", len(kb)) + kb
                var += struct.pack("<H", len(vb)) + vb
            return struct.pack("<BHB", 0, len(var), 0) + var

        sock, w = self._run([packet()])
        self.assertEqual(w.handle_uwsgi.call_count, 1)
        self.assertEqual(w.handle_http.call_count, 0)

    def test_chunked_request_body(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n"
               b"Transfer-Encoding: chunked\r\n\r\n"
               b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")
        sock, w = self._run([req])
        self.assertEqual(w.handle_http.call_count, 1)
        req_obj = w.handle_http.call_args[0][1]
        self.assertEqual(req_obj.body, b"abcde")
        self.assertEqual(req_obj.headers["content-length"], "5")
        self.assertNotIn("transfer-encoding", req_obj.headers)

    def test_content_length_body(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n"
               b"Content-Length: 3\r\n\r\nxyz")
        sock, w = self._run([req])
        self.assertEqual(w.handle_http.call_args[0][1].body, b"xyz")

    def test_unsupported_transfer_encoding_501(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n"
               b"Transfer-Encoding: gzip\r\n\r\nxyz")
        sock, w = self._run([req])
        self.assertTrue(any(b"501" in s for s in sock.sent))
        self.assertEqual(w.handle_http.call_count, 0)

    def test_invalid_content_length_400(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n"
               b"Content-Length: abc\r\n\r\nxyz")
        sock, w = self._run([req])
        self.assertTrue(any(b"400" in s for s in sock.sent))
        self.assertEqual(w.handle_http.call_count, 0)

    def test_socket_timeout_closes_silently(self):
        sock = FakeSock([b"GET / HTTP/1.1\r\nHost: h\r\n\r\n"])
        sock.recv = mock.Mock(side_effect=socket.timeout("idle"))
        w = make_worker()
        w.handle_http = mock.Mock()
        w.handle_request(sock)
        self.assertTrue(sock.closed)
        self.assertEqual(w.handle_http.call_count, 0)

    def test_exception_logged_and_closed(self):
        sock = FakeSock([b"GET / HTTP/1.1\r\nHost: h\r\n\r\n"])
        w = make_worker()

        def boom(sock, req, listener=None, connector=None):
            raise RuntimeError("handler crash")

        w.handle_http = boom
        with mock.patch("asteri.workers.base.logger") as lg:
            w.handle_request(sock)
        lg.error.assert_called()
        self.assertTrue(sock.closed)


class TestAcquireSubmit(unittest.TestCase):
    def test_acquire_connection_full_closes(self):
        w = make_worker(worker_connections=1)
        w.metrics_active_connections = 1
        s = FakeSock([])
        self.assertFalse(w.acquire_connection(s))
        self.assertTrue(s.closed)

    def test_release_connection_floor(self):
        w = make_worker()
        w.release_connection()
        self.assertEqual(w.metrics_active_connections, 0)

    def test_submit_connection_rejects(self):
        w = make_worker(worker_connections=1)
        w.metrics_active_connections = 1
        s = FakeSock([])
        fn = mock.Mock()
        w.submit_connection(fn, s)
        fn.assert_not_called()
        self.assertTrue(s.closed)

    def test_submit_connection_handler_raises(self):
        w = make_worker()
        s = FakeSock([])

        def fn(sock, **kw):
            raise RuntimeError("bad handler")

        with mock.patch("asteri.workers.base.logger"):
            w.submit_connection(fn, s)
        self.assertTrue(any(b"500" in d for d in s.sent))

    def test_guarded_handle_request(self):
        w = make_worker()
        s = FakeSock([])
        w.handle_request = mock.Mock()
        w.guarded_handle_request(s, listener_sock="L")
        w.handle_request.assert_called_once_with(s, listener_sock="L")


class TestLifecycle(unittest.TestCase):
    def test_handle_quit(self):
        w = make_worker()
        w.handle_quit(None, None)
        self.assertFalse(w.alive)

    def test_handle_exit_os_exit_for_sync_like(self):
        class SyncLike(BaseWorker):
            pass

        w = SyncLike(0, 999, [], "dummy:app", 30)
        w.__class__.__name__ = "SyncWorker"
        with mock.patch("os._exit") as ex:
            w.handle_exit(None, None)
        ex.assert_called_once_with(0)

    def test_run_not_implemented(self):
        w = make_worker()
        with self.assertRaises(NotImplementedError):
            w.run()

    def test_handle_http_not_implemented(self):
        w = make_worker()
        with self.assertRaises(NotImplementedError):
            w.handle_http(None, None)

    def test_handle_uwsgi_not_implemented(self):
        w = make_worker()
        with self.assertRaises(NotImplementedError):
            w.handle_uwsgi(None, None)

    def test_init_process_imports_app(self):
        w = make_worker()
        fake_app = mock.Mock()
        with mock.patch("asteri.utils.import_app", return_value=fake_app):
            with mock.patch("asteri.workers.base.signal"):
                with mock.patch("asteri.workers.base.set_proctitle"):
                    w.init_process()
        self.assertEqual(w.app, fake_app)
        self.assertTrue(w.booted)
        self.assertEqual(os.environ.get("ASTERI_DISABLE_DASHBOARD"), "0")

    def test_init_process_dirty_apps(self):
        w = make_worker(dirty_apps="dirty:cfg")
        with mock.patch("asteri.dirty.DirtyAppLoader") as loader:
            with mock.patch("asteri.workers.base.signal"):
                with mock.patch("asteri.workers.base.set_proctitle"):
                    w.init_process()
        loader.assert_called_once_with("dirty:cfg")
        self.assertTrue(w.booted)


class TestMetrics(unittest.TestCase):
    def test_increment_request_metric(self):
        w = make_worker()
        w.increment_request_metric("GET", "HTTP/1.1", 200)
        w.increment_request_metric("GET", "HTTP/1.1", 200)
        w.increment_request_metric("POST", "HTTP/2", 500)
        self.assertEqual(
            w.metrics_requests_total[("GET", "HTTP/1.1", "2xx")], 2)
        self.assertEqual(
            w.metrics_requests_total[("POST", "HTTP/2", "5xx")], 1)

    def test_increment_shared_counter_with_increment(self):
        w = make_worker()
        stash = mock.Mock()
        stash.increment = mock.Mock()
        w.stash = stash
        w.increment_shared_counter("k", 3)
        stash.increment.assert_called_once_with("k", 3)

    def test_increment_shared_counter_no_increment_method(self):
        w = make_worker()
        stash = mock.Mock()
        del stash.increment  # force get/set path
        stash.get.return_value = b"4"
        w.stash = stash
        w.increment_shared_counter("k", 2)
        stash.set.assert_called_once()
        self.assertEqual(stash.set.call_args[0][1], b"6")

    def test_generate_metrics_local(self):
        w = make_worker()
        w.increment_request_metric("GET", "HTTP/1.1", 200)
        text = w.generate_prometheus_metrics()
        self.assertIn("asteri_requests_total{method=\"GET\",protocol=\"HTTP/1.1\",status_class=\"2xx\"} 1", text)
        self.assertIn("http_server_duration_milliseconds_count", text)
        self.assertIn("http_flavor=\"1.1\"", text)

    def test_generate_metrics_with_stash(self):
        w = make_worker()
        stash = mock.Mock()
        stash.get.side_effect = lambda k: {
            "metrics.active_connections": b"7",
            "metrics.workers_count": b"3",
            "metrics.requests_total.GET.HTTP/2.2xx": b"9",
        }.get(k)
        w.stash = stash
        w.metrics_requests_total[("GET", "HTTP/2", "2xx")] = 1
        text = w.generate_prometheus_metrics()
        self.assertIn("asteri_workers_count 3", text)
        self.assertIn("asteri_active_connections 7", text)
        self.assertIn("http_flavor=\"2.0\"", text)
        self.assertIn("} 9", text)

    def test_cached_metrics_cache_hit(self):
        w = make_worker()
        w.generate_prometheus_metrics = mock.Mock(return_value="body")
        first = w._cached_metrics()
        second = w._cached_metrics()
        self.assertEqual(first, second)
        self.assertEqual(w.generate_prometheus_metrics.call_count, 1)

    def test_flush_metrics_not_due(self):
        w = make_worker()
        stash = mock.Mock()
        stash.increment = mock.Mock()
        w.stash = stash
        w._metrics_pending = {"k": 1}
        w._metrics_last_flush = 0  # never flushed
        with mock.patch("time.time", return_value=1.0):
            w._flush_metrics()  # 1.0 - 0 < 5.0 → skip
        stash.increment.assert_not_called()

    def test_flush_metrics_force(self):
        w = make_worker()
        stash = mock.Mock()
        stash.increment = mock.Mock()
        w.stash = stash
        w._metrics_pending = {"k": 1}
        with mock.patch("time.time", return_value=1.0):
            w._flush_metrics(force=True)
        stash.increment.assert_called_once_with("k", 1)


class TestCoverageGaps(unittest.TestCase):
    def _run(self, chunks, **kw):
        sock = FakeSock(chunks)
        w = make_worker(**kw)
        w.handle_http = mock.Mock()
        w.handle_uwsgi = mock.Mock()
        w.handle_h2_request = mock.Mock(return_value=(200, [], b""))
        w.handle_request(sock)
        return sock, w

    def test_acquire_full_close_oserror(self):
        w = make_worker(worker_connections=1)
        w.metrics_active_connections = 1
        s = FakeSock([])
        s.close = mock.Mock(side_effect=OSError)
        self.assertFalse(w.acquire_connection(s))

    def test_acquire_with_stash_increments(self):
        w = make_worker()
        w.stash = mock.Mock()
        w.increment_shared_counter = mock.Mock()
        self.assertTrue(w.acquire_connection(FakeSock([])))
        w.increment_shared_counter.assert_called_once_with(
            "metrics.active_connections", 1)

    def test_release_with_stash_decrements(self):
        w = make_worker()
        w.stash = mock.Mock()
        w.metrics_active_connections = 1
        w.increment_shared_counter = mock.Mock()
        w.release_connection()
        w.increment_shared_counter.assert_called_once_with(
            "metrics.active_connections", -1)

    def test_submit_handler_error_send_fails(self):
        w = make_worker()
        s = FakeSock([])
        s.sendall = mock.Mock(side_effect=OSError)

        def fn(sock, **kw):
            raise RuntimeError("bad handler")

        with mock.patch("asteri.workers.base.logger"):
            w.submit_connection(fn, s)
        s.sendall.assert_called_once()

    @staticmethod
    def make_v2_proxy():
        hdr = b"\r\n\r\n\x00\r\nQUIT\n"
        hdr += bytes([0x21, 0x11])
        hdr += (12).to_bytes(2, "big")
        hdr += socket.inet_aton("10.0.0.1")
        hdr += socket.inet_aton("10.0.0.2")
        hdr += (555).to_bytes(2, "big")
        hdr += (80).to_bytes(2, "big")
        return hdr

    def test_proxy_v2_second_recv_empty_returns(self):
        sock, w = self._run([self.make_v2_proxy()], proxy_protocol=True)
        self.assertEqual(w.handle_http.call_count, 0)
        self.assertTrue(sock.closed)

    def test_proxy_v2_request_ok(self):
        req = b"GET / HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n"
        sock, w = self._run([self.make_v2_proxy(), req], proxy_protocol=True)
        self.assertEqual(w.handle_http.call_count, 1)

    def test_proxy_v2_large_second_recv_431(self):
        big = b"GET / HTTP/1.1\r\nHost: h\r\nX: " + b"Y" * 40000 + b"\r\n\r\n"
        sock, w = self._run([self.make_v2_proxy(), big], proxy_protocol=True)
        self.assertTrue(any(b"431" in s for s in sock.sent))
        self.assertEqual(w.handle_http.call_count, 0)

    def test_dashboard_no_keepalive_returns(self):
        sock, w = self._run(
            [b"GET /asteri-status HTTP/1.1\r\nHost: h\r\n\r\n"], keep_alive=0)
        self.assertIn(b"text/html", b"".join(sock.sent))
        self.assertEqual(w.handle_http.call_count, 0)

    def test_metrics_no_keepalive_returns(self):
        sock, w = self._run(
            [b"GET /metrics HTTP/1.1\r\nHost: h\r\n\r\n"], keep_alive=0)
        self.assertIn(b"text/plain; version=0.0.4", b"".join(sock.sent))
        self.assertEqual(w.handle_http.call_count, 0)

    def test_uwsgi_large_packet_split(self):
        var = b""
        for k, v in {"HTTP_X": "\r\n\r\n", "REQUEST_METHOD": "GET",
                     "PATH_INFO": "/", "HTTP_A": "B"}.items():
            kb, vb = k.encode(), v.encode()
            var += struct.pack("<H", len(kb)) + kb
            var += struct.pack("<H", len(vb)) + vb
        packet = struct.pack("<BHB", 0, len(var), 0) + var
        sock, w = self._run([packet[:18], packet[18:]])
        self.assertEqual(w.handle_uwsgi.call_count, 1)

    def test_uwsgi_packet_incomplete_breaks(self):
        var = b""
        for k, v in {"HTTP_X": "\r\n\r\n", "REQUEST_METHOD": "GET",
                     "PATH_INFO": "/", "HTTP_A": "B"}.items():
            kb, vb = k.encode(), v.encode()
            var += struct.pack("<H", len(kb)) + kb
            var += struct.pack("<H", len(vb)) + vb
        packet = struct.pack("<BHB", 0, len(var), 0) + var
        sock, w = self._run([packet[:18]])
        self.assertEqual(w.handle_uwsgi.call_count, 0)

    def test_finally_close_oserror_swallowed(self):
        sock = FakeSock([b"GET / HTTP/1.1\r\nHost: h\r\n\r\n"])
        sock.close = mock.Mock(side_effect=OSError)
        w = make_worker()
        w.handle_http = mock.Mock()
        w.handle_request(sock)

    def test_negative_content_length_400(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\n"
               b"Content-Length: -1\r\n\r\nxyz")
        sock, w = self._run([req])
        self.assertTrue(any(b"400" in s for s in sock.sent))
        self.assertEqual(w.handle_http.call_count, 0)

    def test_flush_metrics_increment_raises_swallowed(self):
        w = make_worker()
        w.stash = mock.Mock()
        w._metrics_pending = {"k": 1}
        w.increment_shared_counter = mock.Mock(
            side_effect=RuntimeError("stash down"))
        with mock.patch("time.time", return_value=100.0):
            w._flush_metrics(force=True)

    def test_increment_shared_counter_no_stash(self):
        w = make_worker()
        w.increment_shared_counter("k", 1)

    def test_increment_shared_counter_stash_error(self):
        w = make_worker()
        stash = mock.Mock()
        del stash.increment
        stash.get.side_effect = RuntimeError("down")
        w.stash = stash
        w.increment_shared_counter("k", 1)

    def test_generate_metrics_stash_bad_active_conn(self):
        w = make_worker()
        stash = mock.Mock()
        stash.get.return_value = b"not-a-number"
        w.stash = stash
        text = w.generate_prometheus_metrics()
        self.assertIn("asteri_active_connections", text)
        self.assertIn("asteri_workers_count", text)

    def test_generate_metrics_bad_bytes_and_missing(self):
        w = make_worker()
        stash = mock.Mock()
        stash.get.side_effect = lambda k: {
            "metrics.active_connections": b"7",
            "metrics.workers_count": b"3",
            "metrics.requests_total.GET.HTTP/1.1.2xx": b"not-a-number",
            "metrics.requests_total.POST.HTTP/1.1.2xx": None,
        }.get(k)
        w.stash = stash
        w.metrics_requests_total = {
            ("GET", "HTTP/1.1", "2xx"): 5,
            ("POST", "HTTP/1.1", "2xx"): 6,
        }
        text = w.generate_prometheus_metrics()
        self.assertIn('status_class="2xx"} 5', text)
        self.assertIn('status_class="2xx"} 6', text)

    def test_generate_metrics_h3_flavor(self):
        w = make_worker()
        w.increment_request_metric("GET", "HTTP/3", 200)
        text = w.generate_prometheus_metrics()
        self.assertIn('http_flavor="3.0"', text)


if __name__ == "__main__":
    import os

    unittest.main()