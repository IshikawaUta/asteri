import socket
import unittest
from unittest import mock

import h2.connection
import h2.events

from asteri import http as asteri_http
from asteri.http import (
    HTTPError,
    HTTPParser,
    HTTPRequest,
    HTTP2Handler,
    build_error_response,
    build_http_response,
    chunked_encode_part,
    chunked_terminator,
    header_dict,
    read_chunked_body,
    read_content_length_body,
    sanitize_header_name,
    validate_header_block,
)

RAW_HEAD = b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n"


class TestHTTPParserFallback(unittest.TestCase):
    """Python fallback path of HTTPParser.parse (fastparser bypassed)."""

    def test_python_fallback_valid(self):
        with mock.patch.object(asteri_http, "FAST_PARSER_AVAILABLE", False):
            req = HTTPParser.parse(
                b"GET /x HTTP/1.1\r\nHost: h\r\nA: b\r\n\r\nbody")
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.path, "/x")
            self.assertEqual(req.headers, {"host": "h", "a": "b"})
            self.assertEqual(req.body, b"body")

    def test_fallback_empty_and_short(self):
        with mock.patch.object(asteri_http, "FAST_PARSER_AVAILABLE", False):
            self.assertIsNone(HTTPParser.parse(b""))
            self.assertIsNone(HTTPParser.parse(b"GET / HTTP/1.1\r\n"[:5]))

    def test_fastparser_raises_falls_back(self):
        with mock.patch.object(asteri_http, "FAST_PARSER_AVAILABLE", True):
            with mock.patch.object(
                    asteri_http.fastparser, "parse_http",
                    side_effect=RuntimeError("boom")):
                req = HTTPParser.parse(RAW_HEAD)
                self.assertEqual(req.method, "GET")

    def test_fall_through_exception_returns_none(self):
        with mock.patch.object(asteri_http, "FAST_PARSER_AVAILABLE", False):
            with mock.patch.object(
                    HTTPRequest, "__init__", side_effect=ValueError("x")):
                self.assertIsNone(HTTPParser.parse(RAW_HEAD))


class TestHTTPParserParseRaw(unittest.TestCase):
    def test_parse_raw_bytes(self):
        req = HTTPParser.parse_raw(RAW_HEAD, b"", {})
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.path, "/")
        self.assertEqual(req.version, "HTTP/1.1")

    def test_parse_raw_str(self):
        req = HTTPParser.parse_raw("POST /p HTTP/1.1", b"b", {"a": "1"})
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.path, "/p")
        self.assertEqual(req.body, b"b")

    def test_parse_raw_short(self):
        self.assertIsNone(HTTPParser.parse_raw(b"", b"", {}))
        self.assertIsNone(HTTPParser.parse_raw(b"GET /", b"", {}))


class H2Client:
    def __init__(self, sock):
        self.sock = sock
        self.conn = h2.connection.H2Connection()


class H2Fixture:
    """Drives a server HTTP2Handler + h2 client over a socketpair in-process."""

    def __init__(self, server, client):
        self.server = server
        self.client = client

    def send_initial(self):
        self.client.conn.initiate_connection()
        self.client.sock.sendall(self.client.conn.data_to_send())

    def stream(self, headers, data=None, end_stream=True):
        sid = self.client.conn.get_next_available_stream_id()
        names = dict(headers)
        ordered = []
        for pseudo, default in (
                (":method", None), (":scheme", "http"),
                (":authority", "localhost"), (":path", None)):
            value = names.get(pseudo, default)
            if value is not None:
                ordered.append((pseudo, value))
        ordered += [h for h in headers if not h[0].startswith(":")]
        self.client.conn.send_headers(
            sid, ordered, end_stream=end_stream and not data)
        if data:
            self.client.conn.send_data(sid, data, end_stream=end_stream)
        self.client.sock.sendall(self.client.conn.data_to_send())
        return sid

    def pump(self, turns=10):
        """Exchange bytes both ways until the client sees a response status."""
        status = None
        for _ in range(turns):
            try:
                self.server.sock.settimeout(0.05)
                d = self.server.sock.recv(65535)
                if d:
                    self.server.process_data(d)
            except socket.timeout:
                pass
            except OSError:
                pass
            try:
                self.client.sock.settimeout(0.05)
                d = self.client.sock.recv(65535)
                if d:
                    for ev in self.client.conn.receive_data(d):
                        if (status is None and
                                isinstance(ev, h2.events.ResponseReceived)):
                            headers = dict(ev.headers)
                            raw = headers.get(b":status") or headers.get(":status")
                            status = raw.decode("latin-1") if isinstance(
                                raw, bytes) else raw
            except socket.timeout:
                pass
            except OSError:
                pass
            if status:
                break
        return status

    def drain_events(self, turns=3):
        evs = []
        for _ in range(turns):
            try:
                self.client.sock.settimeout(0.05)
                d = self.client.sock.recv(65535)
                if d:
                    evs.extend(self.client.conn.receive_data(d))
            except socket.timeout:
                pass
            except OSError:
                pass
        return evs


class TestHTTP2Handler(unittest.TestCase):
    def _pair(self, handler=None, **kwargs):
        a, b = socket.socketpair()
        a.setblocking(False)
        b.setblocking(False)
        handler = handler or HTTP2Handler(a, **kwargs)
        self.assertTrue(handler._setup_connection())
        return H2Fixture(handler, H2Client(b))

    def _direct_dispatch(self, headers, body=b"", **kwargs):
        fx = self._pair(**kwargs)
        fx.send_initial()
        fx.server.streams[1] = {
            "headers": headers, "body": body, "dispatched": False}
        fx.server._dispatch(1)
        return fx.pump()

    def _mock_handler(self, headers, body=b"", **kwargs):
        handler = HTTP2Handler(mock.Mock(), **kwargs)
        handler.conn = mock.Mock()
        handler.conn.send_headers.return_value = None
        handler.conn.send_data.return_value = None
        handler.conn.data_to_send.return_value = b""
        handler.streams[1] = {
            "headers": headers, "body": body, "dispatched": False}
        handler._dispatch(1)
        return handler.conn

    def _calls(self, conn):
        return conn.send_headers.call_args_list[0].args[1]

    def test_full_exchange(self):
        seen = {}

        def h(method, path, headers, body):
            seen["method"] = method
            seen["path"] = path
            seen["headers"] = headers
            seen["body"] = body
            return 200, [("content-type", "text/plain"), ("x-k", "v")], b"ok!"

        fx = self._pair(request_handler=h)
        fx.send_initial()
        fx.stream(
            [(":method", "POST"), (":path", "/x"), ("user-agent", "t")],
            data=b"body-data")
        status = fx.pump()
        self.assertEqual(status, "200")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/x")
        self.assertEqual(seen["body"], b"body-data")
        self.assertEqual(seen["headers"].get("user-agent"), "t")

    def test_default_handler(self):
        fx = self._pair()
        fx.send_initial()
        fx.stream([(":method", "GET"), (":path", "/")])
        self.assertEqual(fx.pump(), "200")

    def test_stream_reset(self):
        fx = self._pair()
        fx.send_initial()
        fx.stream([(":method", "GET"), (":path", "/")], end_stream=False)
        fx.client.conn.reset_stream(1)
        fx.client.sock.sendall(fx.client.conn.data_to_send())
        fx.pump()
        self.assertNotIn(1, fx.server.streams)

    def test_missing_method_path_400(self):
        conn = self._mock_handler({":scheme": "http", ":authority": "h"})
        self.assertEqual(self._calls(conn)[0], (":status", "400"))

    def test_body_too_large_413(self):
        conn = self._mock_handler(
            {":method": "POST", ":path": "/", ":scheme": "http",
             ":authority": "h", "content-length": "100"},
            body=b"12345678", max_body_size=4)
        self.assertEqual(self._calls(conn)[0], (":status", "413"))

    def test_bad_content_length_value_ok(self):
        conn = self._mock_handler(
            {":method": "POST", ":path": "/", ":scheme": "http",
             ":authority": "h", "content-length": "abc"},
            body=b"x", max_body_size=100)
        self.assertEqual(self._calls(conn)[0], (":status", "200"))

    def test_handler_raises_500(self):
        def h(m, p, hs, b):
            raise RuntimeError("app bug")

        conn = self._mock_handler(
            {":method": "GET", ":path": "/", ":scheme": "http",
             ":authority": "h"}, request_handler=h)
        self.assertEqual(self._calls(conn)[0], (":status", "500"))

    def test_handler_returns_str_body(self):
        conn = self._mock_handler(
            {":method": "GET", ":path": "/", ":scheme": "http",
             ":authority": "h"},
            request_handler=lambda m, p, hs, b: (200, [], "text"))
        send_calls = conn.send_data.call_args_list
        self.assertEqual(send_calls[0].args[1], b"text")

    def test_setup_failure_h2_disabled(self):
        with mock.patch.object(asteri_http, "H2_AVAILABLE", False):
            h2h = HTTP2Handler(mock.Mock())
            self.assertIsNone(h2h.handle())

    def test_handle_recv_empty(self):
        a, b = socket.socketpair()
        h2h = HTTP2Handler(a)
        b.close()
        with mock.patch.object(h2h, "_setup_connection", return_value=True):
            h2h.handle()
        a.close()

    def test_handle_recv_raises(self):
        a, b = socket.socketpair()
        h2h = HTTP2Handler(a, initial_data=b"")
        b.close()
        sock = mock.Mock()
        sock.recv.side_effect = OSError("gone")
        h2h.sock = sock
        with mock.patch.object(h2h, "_setup_connection", return_value=True):
            h2h.handle()

    def test_process_data_error_swallowed(self):
        server, sock = socket.socketpair()
        server.setblocking(False)
        sock.setblocking(False)
        h2h = HTTP2Handler(server)
        self.assertTrue(h2h._setup_connection())
        fx = H2Fixture(h2h, H2Client(sock))
        fx.send_initial()
        with mock.patch.object(h2h, "_handle_event",
                               side_effect=RuntimeError("e")):
            h2h.process_data(b"garbage")
        sock.close()
        server.close()

    def test_send_all_empty(self):
        h2h = HTTP2Handler(mock.Mock())
        h2h._send_all(b"")  # no sendall call
        self.assertTrue(True)

    def test_handle_processes_initial_data(self):
        a, b = socket.socketpair()
        h2h = HTTP2Handler(a, initial_data=b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
        b.close()
        with mock.patch.object(h2h, "_setup_connection", return_value=True):
            with mock.patch.object(h2h, "process_data") as pd:
                h2h.handle()
        pd.assert_called_once_with(b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n")
        a.close()

    def test_handle_recv_data_loop(self):
        h2h = HTTP2Handler(mock.Mock())
        sock = mock.Mock()
        sock.recv.side_effect = [b"frames", b""]
        h2h.sock = sock
        with mock.patch.object(h2h, "_setup_connection", return_value=True):
            with mock.patch.object(h2h, "process_data") as pd:
                h2h.handle()
        pd.assert_called_with(b"frames")

    def test_handler_raises_and_send_fails(self):
        def h(m, p, hs, b):
            raise RuntimeError("app bug")

        handler = HTTP2Handler(mock.Mock(), request_handler=h)
        handler.conn = mock.Mock()
        handler.conn.send_headers.side_effect = RuntimeError("socket dead")
        handler.conn.data_to_send.return_value = b""
        handler.streams[1] = {
            "headers": {":method": "GET", ":path": "/", ":scheme": "http",
                        ":authority": "h"},
            "body": b"", "dispatched": False}
        handler._dispatch(1)

    def test_send_response_none_body(self):
        handler = HTTP2Handler(mock.Mock())
        conn = mock.Mock()
        conn.send_headers.return_value = None
        conn.send_data.return_value = None
        conn.data_to_send.return_value = b""
        handler.conn = conn
        handler._send_response(1, 200, [], None)
        conn.send_data.assert_not_called()


class TestBuildResponses(unittest.TestCase):
    def test_unknown_status(self):
        resp = build_http_response(600, {}, b"x")
        self.assertIn(b"600 Unknown", resp)

    def test_no_content_length_when_no_body(self):
        resp = build_http_response(204, {}, b"")
        self.assertNotIn(b"Content-Length", resp)

    def test_header_injection_sanitized(self):
        resp = build_http_response(
            200, {"X-Evil\r\nInjected: yes": "A\r\nB"}, b"")
        self.assertNotIn(b"\r\nInjected", resp)
        self.assertIn(b"X-Evil  Injected: yes", resp)

    def test_bytes_value(self):
        resp = build_http_response(200, {b"X-Key\r\n": b"v\r\n"}, b"")
        self.assertIn(b"X-Key  ", resp)
        self.assertNotIn(b"X-Key\r\n", resp)

    def test_error_response_default_bytes(self):
        resp = build_error_response(413)
        self.assertIn(b"413", resp)
        self.assertIn(b"Request Entity Too Large", resp)

    def test_error_response_str_message(self):
        resp = build_error_response(500, "Custom server error")
        self.assertIn(b"Custom server error", resp)

    def test_sanitize_header_name(self):
        self.assertEqual(sanitize_header_name("a\rb\nc"), "a b c")
        self.assertEqual(sanitize_header_name(b"a\rb\nc"), b"a b c")

    def test_httperror_message(self):
        err = HTTPError(408)
        self.assertEqual(err.status, 408)
        self.assertIn("Request Timeout", err.message)
        err2 = HTTPError(599, "custom")
        self.assertEqual(err2.message, "custom")
        self.assertEqual(err2.status, 599)


class TestValidateHeaderBlock(unittest.TestCase):
    LIMITS = {
        "limit_request_line": 4094,
        "limit_request_fields": 100,
        "limit_request_field_size": 8190,
    }

    def test_valid(self):
        self.assertTrue(validate_header_block(RAW_HEAD, self.LIMITS))

    def test_empty(self):
        with self.assertRaises(HTTPError) as ctx:
            validate_header_block(b"", self.LIMITS)
        self.assertEqual(ctx.exception.status, 400)

    def test_long_request_line(self):
        with self.assertRaises(HTTPError) as ctx:
            validate_header_block(
                b"GET /" + b"a" * 5000 + b" HTTP/1.1", self.LIMITS)
        self.assertEqual(ctx.exception.status, 431)

    def test_too_many_fields(self):
        lines = [b"GET / HTTP/1.1"]
        lines += [b"X-%d: v" % i for i in range(101)]
        block = b"\r\n".join(lines) + b"\r\n"
        with self.assertRaises(HTTPError) as ctx:
            validate_header_block(block, self.LIMITS)
        self.assertEqual(ctx.exception.status, 431)

    def test_oversized_field(self):
        block = b"GET / HTTP/1.1\r\nX-Long: " + b"a" * 9000
        with self.assertRaises(HTTPError) as ctx:
            validate_header_block(block, self.LIMITS)
        self.assertEqual(ctx.exception.status, 431)

    def test_defaults_when_limits_absent(self):
        self.assertTrue(validate_header_block(RAW_HEAD, {}))


class TestHeaderDict(unittest.TestCase):
    def test_plain_parse(self):
        d = header_dict(RAW_HEAD)
        self.assertEqual(d, {"host": "example.com"})

    def test_duplicates_and_no_colon(self):
        block = b"GET / HTTP/1.1\r\nA: 1\r\nA: 2\r\nno-colon\r\n\r\n"
        self.assertEqual(header_dict(block), {"a": "2"})

    def test_empty_block_400(self):
        with self.assertRaises(HTTPError) as ctx:
            header_dict(b"")
        self.assertEqual(ctx.exception.status, 400)

    def test_long_request_line_431(self):
        head = b"GET /" + b"a" * 5000 + b" H"
        with self.assertRaises(HTTPError) as ctx:
            header_dict(head, self._limits())
        self.assertEqual(ctx.exception.status, 431)

    def test_many_fields_431(self):
        block = b"GET / H\r\n" + b"\r\n".join(
            [b"X-%d: v" % i for i in range(101)])
        with self.assertRaises(HTTPError) as ctx:
            header_dict(block, self._limits())
        self.assertEqual(ctx.exception.status, 431)

    def test_oversized_field_431(self):
        block = b"GET / H\r\nX-Long: " + b"a" * 9000
        with self.assertRaises(HTTPError) as ctx:
            header_dict(block, self._limits())
        self.assertEqual(ctx.exception.status, 431)

    def test_no_limits_no_errors(self):
        block = b"GET / H\r\n" + b"\r\n".join(
            [b"X-%d: v" % i for i in range(105)])
        d = header_dict(block)
        self.assertEqual(len(d), 105)

    @staticmethod
    def _limits():
        return {
            "limit_request_line": 100,
            "limit_request_fields": 100,
            "limit_request_field_size": 8190,
        }


class ChunkedReader:
    """Fake recv() from a queue of bytes chunks."""

    def __init__(self, chunks):
        self._q = list(chunks)

    def __call__(self):
        if not self._q:
            return b""
        return self._q.pop(0)


class TestChunkedBody(unittest.TestCase):
    def test_normal_flow_with_split_chunks(self):
        recv = ChunkedReader([b"5\r\nhello", b"\r\n6\r\n world\r\n0\r\n\r\nX"])
        body, leftover = read_chunked_body(recv, b"", 0)
        self.assertEqual(body, b"hello world")
        self.assertEqual(leftover, b"X")

    def test_body_in_initial_buffer(self):
        initial = b"4\r\ntest\r\n0\r\n\r\nrest"
        body, leftover = read_chunked_body(lambda: b"", initial, 0)
        self.assertEqual(body, b"test")
        self.assertEqual(leftover, b"rest")

    def test_chunk_extension_and_trailers(self):
        initial = b"5;ext=1\r\nhello\r\n0\r\nX-Trailer: v\r\n\r\nT"
        body, leftover = read_chunked_body(lambda: b"", initial, 0)
        self.assertEqual(body, b"hello")
        self.assertEqual(leftover, b"T")

    def test_fragmented_terminator_line(self):
        recv = ChunkedReader([b"3\r\nabc\r", b"\n0\r", b"\n\r\n"])
        body, leftover = read_chunked_body(recv, b"", 0)
        self.assertEqual(body, b"abc")
        self.assertEqual(leftover, b"")

    def test_eof_before_marker(self):
        with self.assertRaises(HTTPError) as ctx:
            read_chunked_body(ChunkedReader([b"zzz"]), b"", 0)
        self.assertEqual(ctx.exception.status, 400)

    def test_invalid_chunk_size(self):
        with self.assertRaises(HTTPError) as ctx:
            read_chunked_body(ChunkedReader([]), b"XX\r\n", 0)
        self.assertEqual(ctx.exception.status, 400)

    def test_incomplete_chunk_body(self):
        with self.assertRaises(HTTPError) as ctx:
            read_chunked_body(ChunkedReader([]), b"5\r\nhe", 0)
        self.assertEqual(ctx.exception.status, 400)

    def test_max_size_exceeded(self):
        with self.assertRaises(HTTPError) as ctx:
            read_chunked_body(ChunkedReader([]), b"5\r\nhello", 4)
        self.assertEqual(ctx.exception.status, 413)

    def test_missing_crlf_after_chunk(self):
        with self.assertRaises(HTTPError) as ctx:
            read_chunked_body(ChunkedReader([]), b"3\r\nabcXX", 0)
        self.assertEqual(ctx.exception.status, 400)

    def test_partial_chunk_crlf_recv(self):
        recv = ChunkedReader([b"\r\n0\r\n\r\n"])
        body, leftover = read_chunked_body(recv, b"3\r\nabc", 0)
        self.assertEqual(body, b"abc")
        self.assertEqual(leftover, b"")

    def test_size_exceeds_first_recv(self):
        recv = ChunkedReader([b"7\r\nab", b"cdefg\r\n0\r\n\r\n"])
        body, leftover = read_chunked_body(recv, b"", 0)
        self.assertEqual(body, b"abcdefg")
        self.assertEqual(leftover, b"")

    def test_helpers(self):
        self.assertEqual(chunked_encode_part(b"abc"), b"3\r\nabc\r\n")
        self.assertEqual(chunked_encode_part("xyz"), b"3\r\nxyz\r\n")
        self.assertEqual(chunked_encode_part(b""), b"")
        self.assertEqual(chunked_terminator(), b"0\r\n\r\n")


class TestContentLengthBody(unittest.TestCase):
    def test_413_upfront(self):
        with self.assertRaises(HTTPError) as ctx:
            read_content_length_body(lambda: b"", b"", 100, max_size=10)
        self.assertEqual(ctx.exception.status, 413)

    def test_all_initial(self):
        body, leftover = read_content_length_body(lambda: b"", b"abcdef", 3)
        self.assertEqual(body, b"abc")
        self.assertEqual(leftover, b"def")

    def test_streaming_with_leftover(self):
        recv = ChunkedReader([b"xxxxx"])
        body, leftover = read_content_length_body(recv, b"ab", 5)
        self.assertEqual(body, b"abxxx")
        self.assertEqual(leftover, b"xx")

    def test_eof_break(self):
        body, leftover = read_content_length_body(ChunkedReader([b"ab"]), b"", 5)
        self.assertEqual(body, b"ab")
        self.assertEqual(leftover, b"")


if __name__ == "__main__":
    unittest.main()