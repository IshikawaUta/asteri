import os
import socket
import unittest
from unittest import mock

from asteri.http import HTTPParser
from asteri.workers.sync import SyncWorker


class CollectSock:
    """Records everything sent to it and returns canned recv data."""

    def __init__(self, recv_chunks=()):
        self.sent = []
        self.recv_chunks = list(recv_chunks)
        self.peername = ("127.0.0.1", 54321)

    def sendall(self, data):
        self.sent.append(data)

    def recv(self, size=8192):
        if not self.recv_chunks:
            return b""
        data = self.recv_chunks[0]
        if len(data) <= size:
            self.recv_chunks.pop(0)
            return data
        self.recv_chunks[0] = data[size:]
        return data[:size]

    def getpeername(self):
        return self.peername

    def fileno(self):
        return 7

    def getsockname(self):
        return ("127.0.0.1", 8000)


def make_worker(app):
    w = SyncWorker(age=0, ppid=999, sockets=[], app_path="dummy:app", timeout=30)
    w.app = app
    return w


def get_request(path="/", headers=None, body=b""):
    hs = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    raw = f"GET {path} HTTP/1.1\r\nHost: h\r\n{hs}\r\n".encode() + body
    return HTTPParser.parse(raw)


class TestExecuteWSGI(unittest.TestCase):
    def test_streaming_generator_chunked(self):
        sock = CollectSock()

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain"),
                                      ("Content-Length", "999")])

            def gen():
                yield b"aa"
                yield "bb"
                yield memoryview(b"cc")
                yield b""
                yield b"dd"

            return gen()

        w = make_worker(app)
        w.execute_wsgi(sock, {"REQUEST_METHOD": "GET", "PATH_INFO": "/",
                              "SERVER_PROTOCOL": "HTTP/1.1"})
        out = b"".join(sock.sent)
        self.assertIn(b"HTTP/1.1 200 OK", out)
        self.assertIn(b"Transfer-Encoding: chunked", out)
        self.assertNotIn(b"Content-Length", out)
        self.assertIn(b"2\r\naa\r\n", out)
        self.assertIn(b"2\r\nbb\r\n", out)
        self.assertIn(b"2\r\ncc\r\n", out)
        self.assertIn(b"2\r\ndd\r\n", out)
        self.assertTrue(out.endswith(b"0\r\n\r\n"))

    def test_list_result_not_streamed(self):
        sock = CollectSock()

        def app(environ, start_response):
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"full-body"]

        w = make_worker(app)
        w.execute_wsgi(sock, {"REQUEST_METHOD": "GET", "PATH_INFO": "/",
                              "SERVER_PROTOCOL": "HTTP/1.1"})
        out = b"".join(sock.sent)
        self.assertIn(b"full-body", out)
        self.assertIn(b"Content-Length: 9", out)

    def test_no_start_response_500(self):
        sock = CollectSock()

        def app(environ, start_response):
            return [b"body"]

        w = make_worker(app)
        w.execute_wsgi(sock, {"REQUEST_METHOD": "GET", "PATH_INFO": "/",
                              "SERVER_PROTOCOL": "HTTP/1.1"})
        out = b"".join(sock.sent)
        self.assertIn(b"500", out)
        self.assertIn(b"failed to start response", out)

    def test_app_raises_500(self):
        sock = CollectSock()

        def app(environ, start_response):
            raise RuntimeError("boom")

        w = make_worker(app)
        w.execute_wsgi(sock, {"REQUEST_METHOD": "GET", "PATH_INFO": "/",
                              "SERVER_PROTOCOL": "HTTP/1.1"})
        out = b"".join(sock.sent)
        self.assertIn(b"500", out)
        self.assertIn(b"WSGI Error", out)

    def test_exception_in_metric_path_swallowed(self):
        sock = CollectSock()

        def app(environ, start_response):
            raise RuntimeError("boom")

        w = make_worker(app)
        with mock.patch.object(w, "increment_request_metric",
                               side_effect=Exception("metrics broken")):
            w.execute_wsgi(sock, {"REQUEST_METHOD": "GET", "PATH_INFO": "/",
                                  "SERVER_PROTOCOL": "HTTP/1.1"})
        out = b"".join(sock.sent)
        self.assertIn(b"500", out)


class TestWSGIInput(unittest.TestCase):
    def test_read_all_from_socket(self):
        sock = CollectSock(recv_chunks=[b"world"])
        w = make_worker(lambda e, s: [])
        req = get_request(headers={"content-length": "10"}, body=b"hello")
        env = w.build_wsgi_environ(req, CollectSock(), sock)
        wsgi_in = env["wsgi.input"]
        self.assertEqual(wsgi_in.read(-1), b"helloworld")

    def test_read_partial_streaming_and_readline(self):
        sock = CollectSock(recv_chunks=[b"xyz"])
        w = make_worker(lambda e, s: [])
        req = get_request(headers={"content-length": "5"}, body=b"ab")
        env = w.build_wsgi_environ(req, CollectSock(), sock)
        wsgi_in = env["wsgi.input"]
        self.assertEqual(wsgi_in.read(2), b"ab")
        self.assertEqual(wsgi_in.read(2), b"xy")
        self.assertEqual(wsgi_in.read(2), b"z")

    def test_readline(self):
        w = make_worker(lambda e, s: [])
        req = get_request(headers={"content-length": "4"}, body=b"a\nb")
        env = w.build_wsgi_environ(req, CollectSock(), CollectSock())
        self.assertEqual(env["wsgi.input"].readline(), b"a\n")


class TestHandleH2(unittest.TestCase):
    def _h2(self, app):
        return make_worker(app)

    def test_h2_happy(self):
        seen = {}

        def app(environ, start_response):
            seen["env"] = environ
            start_response("202 Accepted", [("X-H2", "yes")])
            return [b"h2 body"]

        w = self._h2(app)
        client = CollectSock()
        status, headers, body = w.handle_h2_request(
            "POST", "/p?q=1", {"content-type": "text/plain", "x-custom": "v"},
            b"req-body", listener_sock=CollectSock(), client_sock=client)
        self.assertEqual(status, 202)
        self.assertEqual(body, b"h2 body")
        self.assertEqual(dict(headers)["X-H2"], "yes")
        env = seen["env"]
        self.assertEqual(env["SERVER_PROTOCOL"], "HTTP/2")
        self.assertEqual(env["REQUEST_METHOD"], "POST")
        self.assertEqual(env["PATH_INFO"], "/p")
        self.assertEqual(env["QUERY_STRING"], "q=1")
        self.assertEqual(env["CONTENT_TYPE"], "text/plain")
        self.assertEqual(env["HTTP_X_CUSTOM"], "v")
        self.assertEqual(env["wsgi.input"].read(), b"req-body")

    def test_h2_app_raises(self):
        def app(environ, start_response):
            raise ValueError("nope")

        w = self._h2(app)
        status, headers, body = w.handle_h2_request(
            "GET", "/", {}, b"", CollectSock(), CollectSock())
        self.assertEqual(status, 500)
        self.assertIn(b"WSGI Error", body)

    def test_h2_no_start_response(self):
        w = self._h2(lambda e, s: [b"x"])
        status, headers, body = w.handle_h2_request(
            "GET", "/", {}, b"", CollectSock(), CollectSock())
        self.assertEqual(status, 500)
        self.assertIn(b"failed to start response", body)

    def test_h2_exception_in_metrics(self):
        def app(environ, start_response):
            raise ValueError("nope")

        w = self._h2(app)
        with mock.patch.object(w, "increment_request_metric",
                               side_effect=Exception("no metrics")):
            status, headers, body = w.handle_h2_request(
                "GET", "/", {}, b"", CollectSock(), CollectSock())
        self.assertEqual(status, 500)


class TestHandleUwsgi(unittest.TestCase):
    def test_handle_uwsgi(self):
        sock = CollectSock()

        def app(environ, start_response):
            start_response("200 OK", [("X-U", "1")])
            return [b"u"]

        w = make_worker(app)
        w.handle_uwsgi(sock, {"REQUEST_METHOD": "GET"}, CollectSock())
        out = b"".join(sock.sent)
        self.assertIn(b"200 OK", out)
        self.assertIn(b"u", out)


class TestBuildEnviron(unittest.TestCase):
    def test_proxy_connector(self):
        w = make_worker(lambda e, s: [])
        req = get_request()
        env = w.build_wsgi_environ(
            req, CollectSock(), CollectSock(),
            connector={"proxy_client": ("10.0.0.1", 555),
                       "proxy_server": ("10.0.0.2", 80)})
        self.assertEqual(env["REMOTE_ADDR"], "10.0.0.1")
        self.assertEqual(env["SERVER_NAME"], "10.0.0.2")

    def test_getpeername_exception_fallback(self):
        w = make_worker(lambda e, s: [])
        req = get_request()

        class BadSock(CollectSock):
            def getpeername(self):
                raise OSError("gone")

        env = w.build_wsgi_environ(req, CollectSock(), BadSock())
        self.assertEqual(env["REMOTE_ADDR"], "127.0.0.1")

    def test_ssl_detection(self):
        import types

        w = make_worker(lambda e, s: [])
        req = get_request()

        class SSLSock(CollectSock):
            pass

        fake_ssl = types.SimpleNamespace(SSLSocket=SSLSock)
        with mock.patch("asteri.workers.sync.ssl", fake_ssl):
            env = w.build_wsgi_environ(req, CollectSock(), SSLSock())
        self.assertEqual(env["wsgi.url_scheme"], "https")


class TestRunLoop(unittest.TestCase):
    def _worker(self, socks):
        return SyncWorker(age=0, ppid=os.getppid(), sockets=socks,
                          app_path="dummy:app", timeout=30)

    def test_run_loop_full_iteration(self):
        tcp = socket.socket()
        tcp.bind(("127.0.0.1", 0))
        tcp.listen(1)
        client = socket.socket()
        client.connect(tcp.getsockname())

        class FakeUDP:
            type = socket.SOCK_DGRAM

            def __init__(self, recvs):
                self.recvs = recvs

            def recvfrom(self, n):
                item = self.recvs.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        udp = FakeUDP([BlockingIOError("again"),
                       (b"x", ("127.0.0.1", 123))])

        w = self._worker([tcp, udp])
        w.guarded_handle_request = mock.Mock()

        calls = []

        def fake_select(r, w_, x_, timeout=None):
            n = len(calls)
            calls.append(n)
            if n == 0:
                return ([tcp], [], [])
            if n == 1:
                return ([udp], [], [])
            if n == 2:
                return ([udp], [], [])
            if n == 3:
                raise socket.timeout("t")
            if n == 4:
                raise ValueError("boom")
            w.alive = False
            return ([], [], [])

        loop_mock = mock.Mock()
        try:
            with mock.patch("asteri.workers.sync.select.select",
                            side_effect=fake_select):
                with mock.patch("asteri.workers.sync.asyncio.new_event_loop",
                                return_value=loop_mock):
                    with mock.patch("asteri.workers.sync.threading.Thread"):
                        with mock.patch(
                                "asteri.workers.sync.asyncio.run_coroutine_threadsafe"):
                            with mock.patch("asteri.http3.HTTP3Handler"):
                                with mock.patch("asteri.workers.sync.time.sleep"):
                                    w.run()
        finally:
            client.close()
            tcp.close()

        self.assertEqual(w.guarded_handle_request.call_count, 1)
        self.assertTrue(hasattr(w, "_h3_loop"))
        self.assertTrue(hasattr(w, "_h3_handler"))
        self.assertFalse(w.alive)

    def test_run_loop_breaks_when_parent_dies(self):
        from asteri.workers.sync import SyncWorker as SW

        w = SW(age=0, ppid=-999, sockets=[], app_path="dummy:app", timeout=30)
        with mock.patch("asteri.workers.sync.select.select",
                        return_value=([], [], [])):
            with mock.patch("asteri.workers.sync.os.getppid",
                            return_value=123):
                w.run()
        self.assertFalse(w.alive)


class TestHandleHTTP(unittest.TestCase):
    def test_handle_http_dispatches_wsgi(self):
        sock = CollectSock()

        def app(environ, start_response):
            start_response("200 OK", [("X", "1")])
            return [b"body"]

        w = make_worker(app)
        w.handle_http(sock, get_request(), CollectSock())
        out = b"".join(sock.sent)
        self.assertIn(b"200 OK", out)
        self.assertIn(b"body", out)


class TestH2Edges(unittest.TestCase):
    def test_h2_no_socks_fallbacks_and_non_iter(self):
        seen = {}

        def app(environ, start_response):
            seen["env"] = environ
            write = start_response("200 OK", [("Content-Type", "x")])
            write(b"data")
            return None

        w = make_worker(app)
        status, headers, body = w.handle_h2_request(
            "GET", "/", {"content-length": "4"}, b"", None, None)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"data")
        env = seen["env"]
        self.assertEqual(env["SERVER_NAME"], "127.0.0.1")
        self.assertEqual(env["REMOTE_ADDR"], "127.0.0.1")
        self.assertEqual(env["CONTENT_LENGTH"], "4")

    def test_h2_result_close_called(self):
        class It:
            closed = False

            def __iter__(self):
                return iter([b"x"])

            def close(self):
                self.closed = True

        def app(environ, start_response):
            start_response("299 OK", [])
            return It()

        w = make_worker(app)
        status, headers, body = w.handle_h2_request(
            "GET", "/", {}, b"", CollectSock(), CollectSock())
        self.assertEqual(status, 299)
        self.assertEqual(body, b"x")


class TestEnvironEdges(unittest.TestCase):
    def test_server_addr_fallback_and_content_type(self):
        w = make_worker(lambda e, s: [])
        req = get_request(headers={"content-type": "text/plain"})
        with mock.patch.object(
                w, "_cached_server_addr",
                side_effect=OSError("gone")):
            env = w.build_wsgi_environ(req, CollectSock(), CollectSock())
        self.assertEqual(env["SERVER_NAME"], "127.0.0.1")
        self.assertEqual(env["CONTENT_TYPE"], "text/plain")

    def test_wsgi_input_read_empty_recv_breaks(self):
        w = make_worker(lambda e, s: [])
        req = get_request(headers={"content-length": "5"}, body=b"")
        env = w.build_wsgi_environ(req, CollectSock(),
                                   CollectSock(recv_chunks=[b"ab"]))
        self.assertEqual(env["wsgi.input"].read(-1), b"ab")

    def test_early_hints_oserror_swallowed(self):
        w = make_worker(lambda e, s: [])

        class FailSock(CollectSock):
            def sendall(self, data):
                raise OSError("gone")

        req = get_request()
        env = w.build_wsgi_environ(req, CollectSock(), FailSock())
        env["wsgi.early_hints"]([("Link", "<x>; rel=preload")])  # must not raise

    def test_stream_body_oserror_swallowed(self):
        w = make_worker(lambda e, s: [])

        class FailSock(CollectSock):
            def sendall(self, data):
                raise OSError("gone")

        w._stream_wsgi_body(FailSock(), 200, {"Content-Type": "x"},
                            iter([b"a"]))
        w._stream_wsgi_body(CollectSock(), 200, {"Content-Type": "x"},
                            iter([b"a"]))


if __name__ == "__main__":
    unittest.main()