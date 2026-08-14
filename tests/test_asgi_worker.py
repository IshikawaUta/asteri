import asyncio
import errno
import socket
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from asteri.http import HTTPParser, HTTP2Handler
from asteri.workers.asgi import ASGIWorker

try:
    import h2.connection
    import h2.events
    H2_AVAILABLE = True
except ImportError:
    H2_AVAILABLE = False


class AsyncLoop:
    """Patches the running loop's sock_recv / sock_sendall for capture."""

    def __init__(self, recv_chunks):
        self.recv_chunks = list(recv_chunks)
        self.sent = []

    def __enter__(self):
        self.loop = asyncio.get_running_loop()
        self.orig_recv = self.loop.sock_recv
        self.orig_send = self.loop.sock_sendall

        async def recv(sock, size):
            if not self.recv_chunks:
                return b""
            return self.recv_chunks.pop(0)

        async def sendall(sock, data):
            self.sent.append(data)

        self.loop.sock_recv = recv
        self.loop.sock_sendall = sendall
        return self

    def __exit__(self, *exc):
        self.loop.sock_recv = self.orig_recv
        self.loop.sock_sendall = self.orig_send
        return False


def make_worker(app, **kw):
    w = ASGIWorker(age=0, ppid=999, sockets=[], app_path="dummy:app", timeout=30, **kw)
    w.app = app
    return w


def make_sock():
    s = Mock()
    s.getsockname.return_value = ("127.0.0.1", 8000)
    s.getpeername.return_value = ("1.2.3.4", 99)
    s.type = socket.SOCK_STREAM
    return s


REQ = b"GET /p?q=1 HTTP/1.1\r\nHost: h\r\nx-custom: v\r\n\r\n"


class TestHandleRequest(unittest.TestCase):
    def test_happy_path(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"hi",
                        "more_body": False})

        async def run():
            with AsyncLoop([REQ]) as al:
                w = make_worker(app)
                seen = []
                w.increment_request_metric = Mock(side_effect=lambda *a: seen.append(a))
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"HTTP/1.1 200 OK", out)
            self.assertIn(b"hi", out)
            args = seen[0]
            self.assertEqual(args[0], "GET")
            self.assertEqual(args[2], 200)

        asyncio.run(run())

    def test_no_body_message(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 204,
                        "headers": []})
            await send({"type": "http.response.body", "body": b"p1",
                        "more_body": True})
            await send({"type": "http.response.body", "body": b"p2",
                        "more_body": True})
            await send({"type": "http.response.body", "body": b"",
                        "more_body": False})

        async def run():
            with AsyncLoop([REQ]) as al:
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"204", out)
            self.assertIn(b"p1p2", out)

        asyncio.run(run())

    def test_app_raises_500(self):
        async def app(scope, receive, send):
            raise RuntimeError("boom")

        async def run():
            with AsyncLoop([REQ]):
                w = make_worker(app)
                w.increment_request_metric = Mock()
                await w.handle_asgi_request(make_sock())
            w.increment_request_metric.assert_called()
            self.assertEqual(
                w.increment_request_metric.call_args[0][2], 500)

        asyncio.run(run())

    def test_empty_recv_returns(self):
        async def app(scope, receive, send):
            raise AssertionError("should not run")

        async def run():
            with AsyncLoop([b""]) as al:
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())
            self.assertEqual(al.sent, [])

        asyncio.run(run())

    def test_unparseable_request_returns(self):
        async def run():
            with AsyncLoop([b"GARBAGE\r\n\r\n"]):
                w = make_worker(None)
                w.app = Mock()
                await w.handle_asgi_request(make_sock())
            w.app.assert_not_called()

        asyncio.run(run())

    def test_header_block_invalid_400(self):
        async def run():
            bad = b"GET / HTTP/1.1\r\nHost: a\r\nX: 1\r\nY: 2\r\n\r\n"
            with AsyncLoop([bad]) as al:
                w = make_worker(None, limit_request_fields=2)
                w.app = Mock()
                await w.handle_asgi_request(make_sock())
            self.assertIn(b"431", b"".join(al.sent))
            w.app.assert_not_called()

        asyncio.run(run())

    def test_metrics_endpoint(self):
        async def run():
            with AsyncLoop([b"GET /metrics HTTP/1.1\r\nHost: h\r\n\r\n"]) as al:
                w = make_worker(None)
                w.app = Mock()
                w._cached_metrics = Mock(return_value=b"dummy 1\n")
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"200", out)
            self.assertIn(b"dummy 1", out)
            w.app.assert_not_called()

        asyncio.run(run())

    def test_status_endpoint(self):
        async def run():
            with AsyncLoop([b"GET /asteri-status HTTP/1.1\r\nHost: h\r\n\r\n"]) as al:
                w = make_worker(None)
                w.app = Mock()
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"200", out)
            self.assertIn(b"text/html", out)
            w.app.assert_not_called()

        asyncio.run(run())

    def test_status_endpoint_disabled(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": []})
            await send({"type": "http.response.body", "body": b"real",
                        "more_body": False})

        async def run():
            with AsyncLoop([b"GET /asteri-status HTTP/1.1\r\nHost: h\r\n\r\n"]) as al:
                w = make_worker(app, disable_dashboard=True)
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"real", out)

        asyncio.run(run())

    def test_proxy_protocol_path(self):
        proxy = (b"PROXY TCP4 10.0.0.1 10.0.0.2 555 80\r\n" + REQ.split(b"\r\n\r\n")[0]
                 + b"\r\n\r\n")

        async def app(scope, receive, send):
            self.assertEqual(scope["client"], ("10.0.0.1", 555))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok",
                        "more_body": False})

        async def run():
            with AsyncLoop([proxy]) as al:
                w = make_worker(app, proxy_protocol=True)
                await w.handle_asgi_request(make_sock())
            self.assertIn(b"ok", b"".join(al.sent))

        asyncio.run(run())

    def test_proxy_protocol_second_recv(self):
        head = b"PROXY TCP4 10.0.0.1 10.0.0.2 555 80\r\n"
        rest = b"GET / HTTP/1.1\r\nHost: h\r\n\r\n"

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok",
                        "more_body": False})

        async def run():
            with AsyncLoop([head, rest]) as al:
                w = make_worker(app, proxy_protocol=True)
                await w.handle_asgi_request(make_sock())
            self.assertIn(b"ok", b"".join(al.sent))

        asyncio.run(run())

    def test_early_hints_and_response(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.early_hints",
                        "headers": [(b"Link", b"</x.css>; rel=preload")]})
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"fin",
                        "more_body": False})

        async def run():
            with AsyncLoop([REQ]) as al:
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"HTTP/1.1 103 Early Hints", out)
            self.assertIn(b"</x.css>; rel=preload", out)
            self.assertIn(b"200 OK", out)

        asyncio.run(run())


class TestRequestBody(unittest.TestCase):
    async def _run(self, req_bytes, remaining_chunks=(), **kw):
        with AsyncLoop([req_bytes] + list(remaining_chunks)) as al:
            w = make_worker(None, **kw)
            body = {}
            async def app(scope, receive, send):
                msg = await receive()
                body["data"] = msg["body"]
                await send({"type": "http.response.start", "status": 200,
                            "headers": []})
                await send({"type": "http.response.body", "body": b"r",
                            "more_body": False})
            w.app = app
            await w.handle_asgi_request(make_sock())
        return body, b"".join(al.sent)

    def _sync(self, req_bytes, remaining_chunks=(), **kw):
        return asyncio.run(self._run(req_bytes, remaining_chunks, **kw))

    def test_content_length_full_in_initial(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: 3\r\n\r\nabc"
        body, out = self._sync(req)
        self.assertEqual(body["data"], b"abc")
        self.assertIn(b"200", out)

    def test_content_length_streamed_from_socket(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: 6\r\n\r\nabc"
        body, out = self._sync(req, [b"def"])
        self.assertEqual(body["data"], b"abcdef")
        self.assertIn(b"200", out)

    def test_content_length_too_large_413(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: 10\r\n\r\n"
        body, out = self._sync(req, max_body_size=5)
        self.assertIn(b"413", out)

    def test_content_length_invalid_400(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: abc\r\n\r\n"
        body, out = self._sync(req)
        self.assertIn(b"400", out)

    def test_content_length_negative_400(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: -1\r\n\r\n"
        body, out = self._sync(req)
        self.assertIn(b"400", out)

    def test_chunked_body(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n")
        body, out = self._sync(req)
        self.assertEqual(body["data"], b"abcde")
        self.assertIn(b"200", out)

    def test_chunked_spread_across_recvs(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"4\r\nab")
        body, out = self._sync(req, [b"cd\r\n0\r\n\r\n"])
        self.assertEqual(body["data"], b"abcd")
        self.assertIn(b"200", out)

    def test_chunked_incomplete_400(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"3\r\nabc")
        body, out = self._sync(req)
        self.assertIn(b"400", out)

    def test_chunked_bad_size_400(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"zz\r\nabc\r\n0\r\n\r\n")
        body, out = self._sync(req)
        self.assertIn(b"400", out)

    def test_chunked_too_large_413(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"5\r\nabcde\r\n0\r\n\r\n")
        body, out = self._sync(req, max_body_size=3)
        self.assertIn(b"413", out)

    def test_chunked_size_line_split_across_recv(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"3")
        body, out = self._sync(req, [b"\r\nabc\r\n0\r\n\r\n"])
        self.assertEqual(body["data"], b"abc")
        self.assertIn(b"200", out)

    def test_chunked_zero_no_trailing_crlf(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"0\r\n")
        body, out = self._sync(req)
        self.assertEqual(body["data"], b"")
        self.assertIn(b"200", out)

    def test_chunked_zero_trailing_from_next_recv(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"0\r\n")
        body, out = self._sync(req, [b"\r\n"])
        self.assertEqual(body["data"], b"")
        self.assertIn(b"200", out)

    def test_chunked_incomplete_mid_body_400(self):
        req = (b"POST / HTTP/1.1\r\nHost: h\r\ntransfer-encoding: chunked\r\n\r\n"
               b"4\r\nab")
        body, out = self._sync(req)
        self.assertIn(b"400", out)

    def test_content_length_incomplete_mid_read(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: 5\r\n\r\nabc"
        body, out = self._sync(req)
        self.assertEqual(body["data"], b"abc")
        self.assertIn(b"200", out)

    def test_content_length_oversized_mid_read_413(self):
        req = b"POST / HTTP/1.1\r\nHost: h\r\ncontent-length: 5\r\n\r\n"
        body, out = self._sync(req, [b"abcdef"], max_body_size=5)
        self.assertIn(b"413", out)


class TestWebsocketReaderException(unittest.TestCase):
    def test_reader_exception_sends_disconnect_1006(self):
        seen = []

        async def app(scope, receive, send):
            await receive()
            await send({"type": "websocket.accept"})
            seen.append(await receive())

        async def run():
            http_head = (b"GET /ws HTTP/1.1\r\nHost: h\r\n"
                         b"upgrade: websocket\r\nconnection: upgrade\r\n"
                         b"sec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
            with patch("asteri.utils.parse_websocket_frame",
                       side_effect=ValueError("bad frame")):
                with AsyncLoop([http_head, b"\x81\x00"]):
                    w = make_worker(app)
                    await w.handle_asgi_request(make_sock())

        asyncio.run(run())
        self.assertEqual(seen[0]["type"], "websocket.disconnect")
        self.assertEqual(seen[0]["code"], 1006)


class TestWebsocket(unittest.TestCase):
    def _frame(self, payload, opcode=1):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        b = bytearray()
        b.append(0x80 | opcode)
        if len(payload) <= 125:
            b.append(0x80 | len(payload))
        elif len(payload) <= 65535:
            b.append(0x80 | 126)
            b.extend(len(payload).to_bytes(2, "big"))
        else:
            b.append(0x80 | 127)
            b.extend(len(payload).to_bytes(8, "big"))
        mask = b"\x11\x22\x33\x44"
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return bytes(b) + mask + masked

    def test_websocket_full_conversation(self):
        msgs = []

        async def app(scope, receive, send):
            self.assertEqual(scope["type"], "websocket")
            self.assertEqual(scope["path"], "/ws")
            ev = await receive()
            msgs.append(ev)
            self.assertEqual(ev["type"], "websocket.connect")
            await send({"type": "websocket.accept"})
            ev = await receive()
            self.assertEqual(ev["type"], "websocket.receive")
            self.assertEqual(ev["text"], "hello")
            await send({"type": "websocket.send", "text": "world"})
            await send({"type": "websocket.close", "code": 1000})

        async def run():
            http_head = (b"GET /ws HTTP/1.1\r\nHost: h\r\n"
                         b"upgrade: websocket\r\nconnection: upgrade\r\n"
                         b"sec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                         b"sec-websocket-protocol: chat, superchat\r\n\r\n")
            text_frame = self._frame("hello", opcode=1)
            with AsyncLoop([http_head, text_frame]) as al:
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            self.assertIn(b"HTTP/1.1 101 Switching Protocols", out)
            self.assertIn(b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", out)
            recvd = parse_frames(out)
            opcodes = [o for o, _ in recvd]
            self.assertIn(1, opcodes)

        asyncio.run(run())

    def test_websocket_binary_and_ping(self):
        async def app(scope, receive, send):
            await receive()  # connect
            await send({"type": "websocket.accept"})
            while True:
                ev = await receive()
                if ev["type"] == "websocket.disconnect":
                    return
                if "bytes" in ev:
                    await send({"type": "websocket.send", "bytes": ev["bytes"]})

        async def run():
            http_head = (b"GET /ws HTTP/1.1\r\nHost: h\r\n"
                         b"upgrade: websocket\r\nconnection: upgrade\r\n"
                         b"sec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
            bin_frame = self._frame(b"\x01\x02", opcode=2)
            ping_frame = self._frame(b"ping", opcode=9)
            close_frame = self._frame(b"", opcode=8)
            with AsyncLoop([http_head, bin_frame, ping_frame, close_frame]) as al:
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())
                out = b"".join(al.sent)
            recvd = parse_frames(out)
            opcodes = [o for o, _ in recvd]
            self.assertIn(10, opcodes)  # pong
            self.assertIn(2, opcodes)   # binary echo

        asyncio.run(run())

    def test_websocket_disconnect_closes(self):
        async def app(scope, receive, send):
            await receive()
            await send({"type": "websocket.accept"})
            ev = await receive()
            self.assertEqual(ev["type"], "websocket.disconnect")

        async def run():
            http_head = (b"GET /ws HTTP/1.1\r\nHost: h\r\n"
                         b"upgrade: websocket\r\nconnection: upgrade\r\n"
                         b"sec-websocket-key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
            with AsyncLoop([http_head, b""]):
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())

        asyncio.run(run())


def parse_frames(data):
    """Split concatenated server frames into (opcode, payload) tuples."""
    from asteri.utils import parse_websocket_frame

    frames = []
    idx = data.find(b"Sec-WebSocket-Accept:")
    if idx == -1:
        return frames
    rest = data[data.find(b"\r\n\r\n", idx) + 4:]
    while rest:
        opcode, payload, rest = parse_websocket_frame(rest)
        if opcode is None:
            break
        frames.append((opcode, payload))
    return frames


class TestScope(unittest.TestCase):
    def test_build_scope_http(self):
        w = make_worker(None)
        req = HTTPParser.parse(REQ)
        scope = w.build_asgi_scope(req, make_sock(), make_sock())
        self.assertEqual(scope["method"], "GET")
        self.assertEqual(scope["path"], "/p")
        self.assertEqual(scope["query_string"], b"q=1")
        self.assertEqual(scope["http_version"], "1.1")

    def test_build_scope_fallback(self):
        w = make_worker(None)
        req = HTTPParser.parse(b"GET / HTTP/1.1\r\nHost: h\r\n\r\n")
        s = Mock()
        s.getsockname.side_effect = OSError
        s.getpeername.side_effect = OSError
        scope = w.build_asgi_scope(req, s, s)
        self.assertEqual(scope["client"], ("127.0.0.1", 0))
        self.assertEqual(scope["server"], ("127.0.0.1", 8000))

    def test_build_scope_explicit_server_addr(self):
        w = make_worker(None)
        req = HTTPParser.parse(REQ)
        scope = w.build_asgi_scope(req, make_sock(), make_sock(),
                                   server_addr=("9.9.9.9", 999))
        self.assertEqual(scope["server"], ("9.9.9.9", 999))

    def test_build_h2_scope(self):
        w = make_worker(None)
        scope = w.build_h2_asgi_scope(
            "POST", "/x?y=1", [(b"user-agent", b"t")], make_sock())
        self.assertEqual(scope["http_version"], "2.0")
        self.assertEqual(scope["path"], "/x")
        self.assertEqual(scope["query_string"], b"y=1")
        self.assertEqual(scope["headers"], [(b"user-agent", b"t")])

    def test_build_h2_scope_fallback(self):
        w = make_worker(None)
        s = Mock()
        s.getsockname.side_effect = OSError
        s.getpeername.side_effect = OSError
        scope = w.build_h2_asgi_scope("GET", "/", [], s)
        self.assertEqual(scope["server"], ("127.0.0.1", 8000))


class TestRunH2App(unittest.TestCase):
    async def _call(self, app, body=b"", **kw):
        loop = asyncio.get_running_loop()
        conn = Mock()
        seen = {}
        loop.sock_sendall = AsyncMock(side_effect=lambda sock, data: seen.setdefault(
            "sent", []).append(data))
        w = make_worker(app, **kw)
        stream = {"id": 1, "headers": {":method": "POST", ":path": "/x"},
                  "body": body, "dispatched": False}
        await w._run_h2_asgi_app(make_sock(), conn, stream, asyncio.Lock())
        return conn, seen

    def test_h2_app_happy(self):
        async def app(scope, receive, send):
            added = b""
            while True:
                msg = await receive()
                added += msg["body"]
                if msg["more_body"] is False:
                    break
            await send({"type": "http.response.start", "status": 201,
                        "headers": [(b"x-c", b"1")]})
            await send({"type": "http.response.body", "body": added})

        async def run():
            conn, seen = await self._call(app, body=b"req-body")
            headers = conn.send_headers.call_args_list[0].args[1]
            self.assertIn((":status", "201"), headers)
            conn.send_data.assert_called_once()

        asyncio.run(run())

    def test_h2_app_raises_500(self):
        async def app(scope, receive, send):
            raise RuntimeError("nope")

        async def run():
            conn, seen = await self._call(app)
            headers = conn.send_headers.call_args_list[0].args[1]
            self.assertIn((":status", "500"), headers)

        asyncio.run(run())

    def test_h2_body_too_large_413(self):
        async def app(scope, receive, send):
            raise AssertionError("app must not run")

        async def run():
            conn, seen = await self._call(app, body=b"x" * 10, max_body_size=5)
            headers = conn.send_headers.call_args_list[0].args[1]
            self.assertIn((":status", "413"), headers)
            conn.send_data.call_args_list[0].args[1] == b"Request Entity Too Large"

        asyncio.run(run())

    def test_h2_receive_second_call_empty(self):
        async def app(scope, receive, send):
            await receive()
            second = await receive()
            self.assertEqual(second["body"], b"")
            await send({"type": "http.response.start", "status": 200,
                        "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def run():
            conn, seen = await self._call(app, body=b"data")
            headers = conn.send_headers.call_args_list[0].args[1]
            self.assertIn((":status", "200"), headers)

        asyncio.run(run())


@unittest.skipUnless(H2_AVAILABLE, "h2 library required")
class TestHTTP2Integration(unittest.TestCase):
    def test_full_h2_exchange(self):
        got = {}

        async def app(scope, receive, send):
            got["scope"] = scope
            got["body"] = b""
            while True:
                msg = await receive()
                got["body"] += msg["body"]
                if msg["more_body"] is False:
                    break
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body",
                        "body": b"hello " + got["body"]})

        async def run():
            a, b = socket.socketpair()
            a.setblocking(False)
            b.setblocking(False)
            loop = asyncio.get_running_loop()
            w = make_worker(app)
            task = asyncio.create_task(w.handle_asgi_http2(a))

            client = h2.connection.H2Connection()
            client.initiate_connection()
            await asyncio.sleep(0.05)
            # Read server preface + settings
            try:
                server_data = await asyncio.wait_for(loop.sock_recv(b, 65535), 0.5)
            except asyncio.TimeoutError:
                server_data = b""
            client.receive_data(server_data)

            sid = client.get_next_available_stream_id()
            client.send_headers(
                sid,
                [(":method", "POST"), (":scheme", "http"),
                 (":authority", "localhost"), (":path", "/h2"), ("x-k", "v")],
                end_stream=False,
            )
            client.send_data(sid, b"payload", end_stream=True)
            await loop.sock_sendall(b, client.data_to_send())

            status = None
            for _ in range(100):
                try:
                    d = await asyncio.wait_for(loop.sock_recv(b, 65535), 0.1)
                except asyncio.TimeoutError:
                    continue
                if not d:
                    break
                for ev in client.receive_data(d):
                    if isinstance(ev, h2.events.ResponseReceived):
                        status = dict(ev.headers).get(b":status")
                if status:
                    break
            task.cancel()

            self.assertEqual(status, b"200")
            self.assertEqual(got["body"], b"payload")

        asyncio.run(run())

    def test_h2_app_error_sends_500(self):
        async def app(scope, receive, send):
            raise RuntimeError("bad app")

        async def run():
            a, b = socket.socketpair()
            a.setblocking(False)
            b.setblocking(False)
            loop = asyncio.get_running_loop()
            w = make_worker(app)
            task = asyncio.create_task(w.handle_asgi_http2(a))
            client = h2.connection.H2Connection()
            client.initiate_connection()
            await asyncio.sleep(0.05)
            try:
                server_data = await asyncio.wait_for(loop.sock_recv(b, 65535), 0.5)
            except asyncio.TimeoutError:
                server_data = b""
            client.receive_data(server_data)
            sid = client.get_next_available_stream_id()
            client.send_headers(
                sid, [(":method", "GET"), (":scheme", "http"),
                      (":authority", "localhost"), (":path", "/")],
                end_stream=True,
            )
            await loop.sock_sendall(b, client.data_to_send())

            status = None
            for _ in range(100):
                try:
                    d = await asyncio.wait_for(loop.sock_recv(b, 65535), 0.1)
                except asyncio.TimeoutError:
                    continue
                if not d:
                    break
                for ev in client.receive_data(d):
                    if isinstance(ev, h2.events.ResponseReceived):
                        status = dict(ev.headers).get(b":status")
                if status:
                    break
            task.cancel()
            self.assertEqual(status, b"500")

        asyncio.run(run())


class TestExitAndRun(unittest.TestCase):
    def test_handle_exit_closes_sockets(self):
        s = make_sock()
        w = make_worker(None)
        w.sockets = [s]
        w.handle_exit(None, None)
        self.assertFalse(w.alive)
        s.close.assert_called_once()

    def test_accept_loop_stops_when_not_alive(self):
        async def run():
            loop = asyncio.get_running_loop()
            loop.sock_accept = AsyncMock()
            s = make_sock()
            s.type = socket.SOCK_STREAM
            w = make_worker(None)
            w.sockets = [s]
            w.alive = False
            await w.accept_loop(s)
            loop.sock_accept.assert_not_called()

        asyncio.run(run())


class _FakeLoop:
    def __init__(self, close_raises=False):
        self.closed = False
        self._close_raises = close_raises

    def run_until_complete(self, coro):
        return asyncio.run(coro)

    def close(self):
        if self._close_raises:
            raise Exception("close boom")
        self.closed = True


class TestRunMainExit(unittest.TestCase):
    def test_run_uses_uvloop_when_available(self):
        loop = _FakeLoop()
        fake_uvloop = types.ModuleType("uvloop")
        fake_uvloop.new_event_loop = Mock(return_value=loop)
        w = make_worker(None)
        with patch.dict(sys.modules, {"uvloop": fake_uvloop}), \
                patch("asyncio.set_event_loop") as set_loop:
            w.run()
        set_loop.assert_called_once()
        self.assertTrue(loop.closed)

    def test_run_falls_back_to_asyncio(self):
        loop = _FakeLoop()
        w = make_worker(None)
        with patch.dict(sys.modules, {"uvloop": None}), \
                patch("asyncio.new_event_loop", return_value=loop), \
                patch("asyncio.set_event_loop"):
            w.run()
        self.assertTrue(loop.closed)

    def test_run_swallows_close_error(self):
        loop = _FakeLoop(close_raises=True)
        fake_uvloop = types.ModuleType("uvloop")
        fake_uvloop.new_event_loop = Mock(return_value=loop)
        w = make_worker(None)
        with patch.dict(sys.modules, {"uvloop": fake_uvloop}), \
                patch("asyncio.set_event_loop"):
            w.run()

    def test_run_rethrows_loop_error_but_closes(self):
        loop = _FakeLoop()
        w = make_worker(None)
        w.main_loop = Mock(side_effect=RuntimeError("boom"))
        fake_uvloop = types.ModuleType("uvloop")
        fake_uvloop.new_event_loop = Mock(return_value=loop)
        with patch.dict(sys.modules, {"uvloop": fake_uvloop}), \
                patch("asyncio.set_event_loop"):
            with self.assertRaises(RuntimeError):
                w.run()
        self.assertTrue(loop.closed)

    def test_handle_exit_sock_close_oserror(self):
        s = make_sock()
        s.close.side_effect = OSError
        w = make_worker(None)
        w.sockets = [s]
        w.handle_exit(None, None)
        self.assertFalse(w.alive)

    def test_main_loop_sets_blocking_and_gathers(self):
        async def run():
            s1, s2 = make_sock(), make_sock()
            w = make_worker(None)
            w.sockets = [s1, s2]
            w.alive = False
            await w.main_loop()
            s1.setblocking.assert_called_with(False)
            s2.setblocking.assert_called_with(False)

        asyncio.run(run())


class _FlipAlive:
    """Truthy on the first read, falsy on every subsequent read."""

    def __init__(self):
        self.reads = 0

    def __bool__(self):
        self.reads += 1
        return self.reads == 1


class TestAcceptLoop(unittest.TestCase):
    def test_returns_when_alive_flips_after_loop_check(self):
        async def run():
            loop = asyncio.get_running_loop()
            loop.sock_accept = AsyncMock()
            w = make_worker(None)
            w.alive = _FlipAlive()
            await w.accept_loop(make_sock())
            loop.sock_accept.assert_not_called()

        asyncio.run(run())

    def test_tcp_accepts_and_dispatches(self):
        async def run():
            loop = asyncio.get_running_loop()
            s = make_sock()
            client = Mock()
            calls = {"n": 0}

            async def fake_accept(sock):
                calls["n"] += 1
                if calls["n"] == 1:
                    return (client, ("1.2.3.4", 88))
                w.alive = False
                raise BlockingIOError

            loop.sock_accept = fake_accept
            w = make_worker(None)
            w.handle_asgi_request = AsyncMock()
            await w.accept_loop(s)
            args = w.handle_asgi_request.call_args
            self.assertEqual(args[0][0], client)
            self.assertEqual(args[1]["listener_sock"], s)
            self.assertEqual(args[1]["server_addr"], ("127.0.0.1", 8000))

        asyncio.run(run())

    def test_accept_loop_interrupted_error(self):
        async def run():
            loop = asyncio.get_running_loop()
            calls = {"n": 0}

            async def fake_accept(sock):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise InterruptedError
                w.alive = False
                raise InterruptedError

            loop.sock_accept = fake_accept
            w = make_worker(None)
            await w.accept_loop(make_sock())

        asyncio.run(run())

    def test_closes_client_when_not_alive(self):
        async def run():
            loop = asyncio.get_running_loop()
            client = Mock()

            async def fake_accept(sock):
                w.alive = False
                return (client, ("1.2.3.4", 88))

            loop.sock_accept = fake_accept
            w = make_worker(None)
            w.handle_asgi_request = AsyncMock()
            await w.accept_loop(make_sock())
            client.close.assert_called_once()

        asyncio.run(run())

    def test_client_close_oserror_swallowed(self):
        async def run():
            loop = asyncio.get_running_loop()
            client = Mock()
            client.close.side_effect = OSError

            async def fake_accept(sock):
                w.alive = False
                return (client, ("1.2.3.4", 88))

            loop.sock_accept = fake_accept
            w = make_worker(None)
            await w.accept_loop(make_sock())

        asyncio.run(run())

    def test_getsockname_oserror_keeps_none_addr(self):
        async def run():
            loop = asyncio.get_running_loop()
            s = make_sock()
            s.getsockname.side_effect = OSError
            calls = {"n": 0}

            async def fake_accept(sock):
                calls["n"] += 1
                if calls["n"] == 1:
                    return (Mock(), ("1.2.3.4", 88))
                w.alive = False
                raise BlockingIOError

            loop.sock_accept = fake_accept
            w = make_worker(None)
            w.handle_asgi_request = AsyncMock()
            await w.accept_loop(s)
            self.assertIsNone(w.handle_asgi_request.call_args[1]["server_addr"])

        asyncio.run(run())

    def test_outer_exception_sleeps_and_returns(self):
        async def run():
            loop = asyncio.get_running_loop()
            calls = {"n": 0}

            async def fake_accept(sock):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("boom")
                w.alive = False
                raise RuntimeError("boom")

            loop.sock_accept = fake_accept
            w = make_worker(None)
            await w.accept_loop(make_sock())

        asyncio.run(run())

    def test_udp_accept_loop(self):
        async def run():
            s = Mock()
            s.type = socket.SOCK_DGRAM
            calls = {"n": 0}

            def fake_recvfrom(size):
                calls["n"] += 1
                if calls["n"] == 1:
                    return (b"ping", ("1.2.3.4", 55))
                if calls["n"] == 2:
                    raise OSError(errno.EAGAIN, "try again")
                if calls["n"] == 3:
                    raise OSError(errno.EWOULDBLOCK, "again")
                if calls["n"] == 4:
                    raise BlockingIOError
                w.alive = False
                raise OSError(999, "fatal")

            s.recvfrom = Mock(side_effect=fake_recvfrom)
            w = make_worker(None)
            with patch("asteri.http3.HTTP3Handler") as H3:
                h = H3.return_value
                h.handle_packet = AsyncMock()
                await w.accept_loop(s)
            h.handle_packet.assert_called_once()

        asyncio.run(run())


class TestEndpointExceptions(unittest.TestCase):
    def test_status_endpoint_exception_swallowed(self):
        async def run():
            with patch("asteri.utils.build_status_html",
                       side_effect=RuntimeError("x")):
                w = make_worker(None)
                await w.handle_asgi_status(make_sock())

        asyncio.run(run())

    def test_metrics_endpoint_exception_swallowed(self):
        async def run():
            w = make_worker(None)
            w._cached_metrics = Mock(side_effect=RuntimeError("x"))
            await w.handle_asgi_metrics(make_sock())

        asyncio.run(run())


class TestRequestEdgeCases(unittest.TestCase):
    def test_connection_cap_returns(self):
        async def run():
            sock = make_sock()
            with AsyncLoop([REQ]):
                w = make_worker(None, worker_connections=1)
                w.metrics_active_connections = 1
                w.app = Mock()
                await w.handle_asgi_request(sock)
            sock.close.assert_called_once()
            w.app.assert_not_called()

        asyncio.run(run())

    def test_proxy_second_recv_empty_returns(self):
        proxy = b"PROXY TCP4 10.0.0.1 10.0.0.2 555 80\r\n"

        async def run():
            with AsyncLoop([proxy]) as al:
                w = make_worker(None, proxy_protocol=True)
                w.app = Mock()
                await w.handle_asgi_request(make_sock())
            self.assertEqual(al.sent, [])
            w.app.assert_not_called()

        asyncio.run(run())

    def test_h2_preface_dispatches_to_http2(self):
        async def run():
            with AsyncLoop([HTTP2Handler.PREFACE + b"more"]):
                w = make_worker(None)
                w.handle_asgi_http2 = AsyncMock()
                await w.handle_asgi_request(make_sock())
            w.handle_asgi_http2.assert_called_once()

        asyncio.run(run())

    def test_early_hints_send_error_swallowed(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.early_hints",
                        "headers": [(b"Link", b"</x.css>; rel=preload")]})
            await send({"type": "http.response.start", "status": 200,
                        "headers": []})
            await send({"type": "http.response.body", "body": b"ok",
                        "more_body": False})

        async def run():
            with AsyncLoop([REQ]) as al:
                loop = asyncio.get_running_loop()
                orig = loop.sock_sendall

                async def flaky(sock, data):
                    if b"103 Early Hints" in data:
                        raise OSError("peer gone")
                    await orig(sock, data)

                loop.sock_sendall = flaky
                w = make_worker(app)
                await w.handle_asgi_request(make_sock())
            out = b"".join(al.sent)
            self.assertIn(b"200 OK", out)

        asyncio.run(run())

    def test_metric_increment_failure_in_except_swallowed(self):
        async def app(scope, receive, send):
            raise RuntimeError("boom")

        async def run():
            with AsyncLoop([REQ]):
                w = make_worker(app)
                w.increment_request_metric = Mock(
                    side_effect=RuntimeError("metric fail"))
                await w.handle_asgi_request(make_sock())

        asyncio.run(run())

    def test_sock_close_oserror_swallowed(self):
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": []})
            await send({"type": "http.response.body", "body": b"ok",
                        "more_body": False})

        async def run():
            sock = make_sock()
            sock.close.side_effect = OSError
            with AsyncLoop([REQ]):
                w = make_worker(app)
                await w.handle_asgi_request(sock)

        asyncio.run(run())


@unittest.skipUnless(H2_AVAILABLE, "h2 library required")
class TestHttp2EdgeCases(unittest.TestCase):
    def test_h2_missing_library_returns(self):
        async def run():
            w = make_worker(None)
            with patch.dict(sys.modules, {
                "h2": None, "h2.connection": None,
                "h2.events": None, "h2.config": None,
            }):
                await w.handle_asgi_http2(make_sock())

        asyncio.run(run())

    def test_h2_initial_data_is_processed(self):
        async def run():
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(return_value=b"")
            w = make_worker(None)
            w.app = Mock()
            conn = Mock()
            conn.data_to_send.return_value = b""
            ev = object.__new__(h2.events.RequestReceived)
            ev.stream_id = 1
            ev.headers = [(b":method", b"GET"), (b":path", b"/")]
            ev.stream_ended = False
            conn.receive_data.return_value = [ev]
            with patch("h2.connection.H2Connection", return_value=conn):
                await w.handle_asgi_http2(make_sock(), initial_data=b"PRI")

        asyncio.run(run())

    def test_h2_process_stream_reset_and_protocol_error(self):
        async def run():
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(
                side_effect=[b"first", b"second", b""])
            w = make_worker(None)
            w.app = Mock()
            conn = Mock()
            conn.data_to_send.return_value = b""
            reset_ev = object.__new__(h2.events.StreamReset)
            reset_ev.stream_id = 7
            conn.receive_data.side_effect = [
                [reset_ev],
                Exception("bad protocol"),
            ]
            with patch("h2.connection.H2Connection", return_value=conn):
                await w.handle_asgi_http2(make_sock())

        asyncio.run(run())

    def test_h2_close_connection_error_swallowed(self):
        async def run():
            loop = asyncio.get_running_loop()
            loop.sock_recv = AsyncMock(return_value=b"")
            w = make_worker(None)
            conn = Mock()
            conn.data_to_send.return_value = b""
            conn.close_connection.side_effect = Exception("close boom")
            with patch("h2.connection.H2Connection", return_value=conn):
                await w.handle_asgi_http2(make_sock())

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()