import asyncio
import os
import socket
import struct
import tempfile
import unittest
from unittest import mock

from asteri.dirty import (
    OP_DELETE,
    OP_ERROR,
    OP_GET,
    OP_INCREMENT,
    OP_NOT_FOUND,
    OP_SET,
    OP_SUCCESS,
    DirtyAppLoader,
    StashClient,
    StashServer,
    TLV,
)


class TestStashProcessRequest(unittest.TestCase):
    def setUp(self):
        self.server = StashServer(("127.0.0.1", 0))

    def test_set_invalid_key(self):
        resp = self.server._process_request(OP_SET, b"x")
        self.assertEqual(TLV.decode(resp)[0], OP_ERROR)

    def test_set_invalid_value(self):
        payload = TLV.encode(1, b"k") + b"\x00"
        resp = self.server._process_request(OP_SET, payload)
        self.assertEqual(TLV.decode(resp)[0], OP_ERROR)

    def test_set_roundtrip(self):
        payload = TLV.encode(1, b"k") + TLV.encode(2, b"v")
        resp = self.server._process_request(OP_SET, payload)
        self.assertEqual(TLV.decode(resp)[0], OP_SUCCESS)
        self.assertEqual(self.server.data[b"k".decode()], b"v")

    def test_get_not_found(self):
        resp = self.server._process_request(OP_GET, b"missing".decode().encode())
        self.assertEqual(TLV.decode(resp)[0], OP_NOT_FOUND)

    def test_delete_not_found(self):
        resp = self.server._process_request(OP_DELETE, b"missing")
        self.assertEqual(TLV.decode(resp)[0], OP_NOT_FOUND)

    def test_increment_invalid_key(self):
        resp = self.server._process_request(OP_INCREMENT, b"short")
        self.assertEqual(TLV.decode(resp)[0], OP_ERROR)

    def test_increment_invalid_delta(self):
        payload = TLV.encode(1, b"k") + b"\x00\x00"  # only 2 bytes < 8
        resp = self.server._process_request(OP_INCREMENT, payload)
        self.assertEqual(TLV.decode(resp)[0], OP_ERROR)

    def test_increment_non_numeric_value(self):
        self.server.data["k"] = b"notanumber"
        payload = TLV.encode(1, b"k") + struct.pack(">q", 5)
        resp = self.server._process_request(OP_INCREMENT, payload)
        t, val, _ = TLV.decode(resp)
        self.assertEqual(t, OP_SUCCESS)
        self.assertEqual(int(val), 5)

    def test_increment_numeric(self):
        self.server.data["k"] = b"10"
        payload = TLV.encode(1, b"k") + struct.pack(">q", 2)
        resp = self.server._process_request(OP_INCREMENT, payload)
        t, val, _ = TLV.decode(resp)
        self.assertEqual(int(val), 12)

    def test_unknown_operation(self):
        resp = self.server._process_request(99, b"")
        self.assertEqual(TLV.decode(resp)[0], OP_ERROR)

    def test_generic_exception_caught(self):
        resp = self.server._process_request(OP_GET, b"\xff\xff\xff")
        self.assertEqual(TLV.decode(resp)[0], OP_ERROR)


class TestStashServerIO(unittest.TestCase):
    def test_start_unix_socket_unlinks_existing(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stash.sock")
            with open(path, "w") as f:
                f.write("stale")
            server = StashServer(path)
            server.start()
            self.assertTrue(os.path.exists(path))
            self.assertFalse(os.path.isfile(path))  # regular file replaced by socket
            server.stop()

    def test_start_unix_socket_unlink_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stash.sock")
            server = StashServer(path)
            with mock.patch("asteri.dirty.os.unlink",
                            side_effect=OSError("rm failed")):
                with mock.patch("asteri.dirty.os.path.exists",
                                return_value=True):
                    server.start()
                    self.assertTrue(server.running)
                    server.stop()

    def test_stop_closes_and_unlinks(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "stash.sock")
            server = StashServer(path)
            server.start()
            server.stop()
            self.assertFalse(server.running)
            self.assertTrue(server.server_sock._closed)

    def test_run_loop_timeout_then_error(self):
        class FakeSock:
            def __init__(self):
                self.calls = 0

            def accept(self):
                self.calls += 1
                if self.calls == 1:
                    raise socket.timeout("t")
                raise OSError("gone")

        server = StashServer(("127.0.0.1", 0))
        server.server_sock = FakeSock()
        server.running = True
        with mock.patch("asteri.dirty.threading.Thread"):
            server._run_loop()

    def test_handle_client_oserror_close(self):
        class FakeClient:
            def settimeout(self, t):
                pass

            def recv(self, n):
                raise OSError("gone")

            def close(self):
                raise OSError("close failed")

        server = StashServer(("127.0.0.1", 0))
        server.running = True
        server._handle_client(FakeClient())

    def test_handle_client_empty_recv_breaks(self):
        class FakeClient:
            def settimeout(self, t):
                pass

            def recv(self, n):
                return b""

            def close(self):
                pass

        server = StashServer(("127.0.0.1", 0))
        server.running = True
        server._handle_client(FakeClient())


class TestTLVEdges(unittest.TestCase):
    def test_decode_incomplete_value(self):
        full = TLV.encode(7, b"hello")
        t, v, rem = TLV.decode(full[:9])  # header ok but value truncated
        self.assertIsNone(t)

    def test_decode_roundtrip(self):
        t, v, rem = TLV.decode(TLV.encode(7, b"hello"))
        self.assertEqual((t, v), (7, b"hello"))
        self.assertEqual(rem, b"")


class TestStashClientIO(unittest.TestCase):
    def test_send_request_connect_oserror(self):
        client = StashClient(("127.0.0.1", 1))
        t, val = client._send_request(OP_GET, b"k")
        self.assertEqual(t, OP_ERROR)
        self.assertTrue(val)

    def test_send_request_no_response(self):
        class FakeSock:
            def connect(self, a):
                pass

            def sendall(self, d):
                pass

            def recv(self, n):
                return b""

            def close(self):
                pass

        client = StashClient("dummy")
        with mock.patch("asteri.dirty.socket.socket", return_value=FakeSock()):
            t, val = client._send_request(OP_GET, b"k")
        self.assertEqual(t, OP_ERROR)
        self.assertEqual(val, b"No response")

    def test_send_request_close_oserror(self):
        server = StashServer(("127.0.0.1", 0))
        server.start()
        port = server.server_sock.getsockname()[1]
        client = StashClient(("127.0.0.1", port))
        with mock.patch("socket.socket.close", side_effect=OSError("x")):
            t, val = client._send_request(OP_GET, b"key")
        self.assertEqual(t, OP_NOT_FOUND)
        server.stop()

    def test_increment_client(self):
        server = StashServer(("127.0.0.1", 0))
        server.start()
        port = server.server_sock.getsockname()[1]
        client = StashClient(("127.0.0.1", port))
        try:
            self.assertTrue(client.increment("counter", 2))
            self.assertTrue(client.increment("counter", 3))
            self.assertEqual(client.get("counter"), b"5")
        finally:
            server.stop()

    def test_delete_client(self):
        server = StashServer(("127.0.0.1", 0))
        server.start()
        port = server.server_sock.getsockname()[1]
        client = StashClient(("127.0.0.1", port))
        try:
            self.assertTrue(client.set("k", b"v"))
            self.assertTrue(client.delete("k"))
            self.assertFalse(client.delete("k"))
        finally:
            server.stop()

    def test_stop_server_sock_close_oserror(self):
        server = StashServer(("127.0.0.1", 0))
        server.server_sock = mock.Mock()
        server.server_sock.close.side_effect = OSError("x")
        server.running = True
        server.stop()


class TestDirtyLoaderEdges(unittest.TestCase):
    def test_empty_mapping(self):
        loader = DirtyAppLoader("")
        self.assertEqual(loader.routes, {})
        self.assertIsNone(loader._match_app("h", "/p"))

    def test_part_without_equals_skipped(self):
        loader = DirtyAppLoader("junk")
        self.assertEqual(loader.routes, {})

    def test_first_route_fallback(self):
        loader = DirtyAppLoader("a.com=app:a")
        app_str = loader._match_app("b.com", "/nope")
        self.assertEqual(app_str, "app:a")

    def test_get_app_empty_str(self):
        loader = DirtyAppLoader("")
        self.assertIsNone(loader._get_app(""))

    @mock.patch("asteri.dirty.import_app")
    def test_wsgi_404(self, mock_import_app):
        loader = DirtyAppLoader("")
        start = mock.Mock()
        body = list(loader.wsgi_call({"HTTP_HOST": "x", "PATH_INFO": "/"},
                                     start))
        start.assert_called_once_with("404 Not Found",
                                      [("Content-Type", "text/plain")])
        self.assertIn(b"No dynamic app routed", b"".join(body))

    @mock.patch("asteri.dirty.import_app")
    def test_asgi_404(self, mock_import_app):
        loader = DirtyAppLoader("")
        send = mock.AsyncMock()

        async def run():
            await loader({"type": "http", "headers": [], "path": "/x"},
                         None, send)

        asyncio.run(run())
        self.assertEqual(send.await_count, 2)

    @mock.patch("asteri.dirty.import_app")
    def test_asgi_non_http_default(self, mock_import_app):
        app = mock.AsyncMock()
        mock_import_app.return_value = app
        loader = DirtyAppLoader("default=def:app")
        send = mock.AsyncMock()

        async def run():
            await loader({"type": "websocket"}, None, send)

        asyncio.run(run())
        app.assert_awaited_once()

    @mock.patch("asteri.dirty.import_app")
    def test_asgi_non_http_no_routes(self, mock_import_app):
        loader = DirtyAppLoader("")

        async def run():
            await loader({"type": "websocket"}, None, mock.AsyncMock())

        asyncio.run(run())
        mock_import_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()