import unittest
from asteri.http import HTTPParser, HTTP2Handler, build_http_response


class TestHTTP(unittest.TestCase):
    def test_parse_valid_get(self):
        raw = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nUser-Agent: test\r\n\r\n"
        req = HTTPParser.parse(raw)
        self.assertIsNotNone(req)
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.path, "/index.html")
        self.assertEqual(req.version, "HTTP/1.1")
        self.assertEqual(req.headers.get("host"), "localhost")
        self.assertEqual(req.headers.get("user-agent"), "test")
        self.assertEqual(req.body, b"")

    def test_parse_valid_post_with_body(self):
        raw = b"POST /submit HTTP/1.1\r\nHost: localhost\r\nContent-Length: 12\r\n\r\nhello world!"
        req = HTTPParser.parse(raw)
        self.assertIsNotNone(req)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.path, "/submit")
        self.assertEqual(req.version, "HTTP/1.1")
        self.assertEqual(req.headers.get("content-length"), "12")
        self.assertEqual(req.body, b"hello world!")

    def test_parse_invalid_requests(self):
        # Empty request
        self.assertIsNone(HTTPParser.parse(b""))
        # Invalid request line (only 2 parts)
        self.assertIsNone(HTTPParser.parse(b"GET / HTTP/1.1\r\n\r\n"[:5]))
        # Invalid request line (only 1 part)
        self.assertIsNone(HTTPParser.parse(b"GET\r\n\r\n"))

    def test_build_http_response_string_body(self):
        headers = {"Content-Type": "text/plain", "X-Custom": "Value"}
        resp = build_http_response(200, headers, "Hello")

        expected_start = b"HTTP/1.1 200 OK\r\n"
        self.assertTrue(resp.startswith(expected_start))
        self.assertIn(b"Content-Type: text/plain\r\n", resp)
        self.assertIn(b"X-Custom: Value\r\n", resp)
        self.assertIn(b"Content-Length: 5\r\n", resp)
        self.assertTrue(resp.endswith(b"\r\n\r\nHello"))

    def test_build_http_response_bytes_body(self):
        headers = {"Content-Type": "application/octet-stream"}
        resp = build_http_response(201, headers, b"\x00\x01\x02")
        self.assertTrue(resp.startswith(b"HTTP/1.1 201 Created\r\n"))
        self.assertIn(b"Content-Length: 3\r\n", resp)
        self.assertTrue(resp.endswith(b"\r\n\r\n\x00\x01\x02"))

    def test_is_http2(self):
        preface = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
        self.assertTrue(HTTP2Handler.is_http2(preface))
        self.assertTrue(HTTP2Handler.is_http2(preface + b"extra data"))
        self.assertFalse(HTTP2Handler.is_http2(b"GET / HTTP/1.1\r\n"))


if __name__ == "__main__":
    unittest.main()
