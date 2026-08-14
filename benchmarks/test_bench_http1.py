"""HTTP/1.1 hot-path benchmarks.

Covers the request parser (C extension and pure-Python fallback), header
handling, body framing (chunked and content-length) and response building.
"""

from contextlib import contextmanager

from asteri import http as http_mod
from asteri.http import (
    HTTPParser,
    HTTP2Handler,
    build_error_response,
    build_http_response,
    chunked_encode_part,
    header_dict,
    read_chunked_body,
    read_content_length_body,
    sanitize_header_name,
    validate_header_block,
)

SMALL_REQUEST = b"GET / HTTP/1.1\r\nHost: localhost:8080\r\n\r\n"

TYPICAL_REQUEST = (
    b"GET /api/v1/users?page=3&per_page=50&sort=created_at HTTP/1.1\r\n"
    b"Host: api.example.com\r\n"
    b"User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    b"(KHTML, like Gecko) Chrome/124.0 Safari/537.36\r\n"
    b"Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8\r\n"
    b"Accept-Encoding: gzip, deflate, br\r\n"
    b"Accept-Language: en-US,en;q=0.9\r\n"
    b"Cache-Control: no-cache\r\n"
    b"Cookie: session=8f14e45fceea167a5a36dedd4bea2543; theme=dark; lang=en\r\n"
    b"Referer: https://api.example.com/dashboard\r\n"
    b"Sec-Fetch-Dest: document\r\n"
    b"Sec-Fetch-Mode: navigate\r\n"
    b"X-Request-Id: 0f9c2d1a-7b3e-4f10-9a2c-6d5b8e1f4a77\r\n"
    b"Connection: keep-alive\r\n\r\n"
)

POST_BODY = b'{"name":"asteri","version":"3.0.0","tags":["wsgi","asgi","http3"]}' * 16
POST_REQUEST = (
    b"POST /api/v1/events HTTP/1.1\r\n"
    b"Host: api.example.com\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: " + str(len(POST_BODY)).encode() + b"\r\n"
    b"Connection: keep-alive\r\n\r\n" + POST_BODY
)

# A request with many headers, close to the default limit of 100 fields.
_MANY_HEADERS = b"".join(
    b"X-Trace-Header-%03d: value-%03d-%s\r\n" % (i, i, b"p" * 32) for i in range(80)
)
LARGE_HEADER_REQUEST = (
    b"GET /deeply/nested/resource/path/segment HTTP/1.1\r\n"
    b"Host: api.example.com\r\n" + _MANY_HEADERS + b"\r\n"
)

HEADER_BLOCK = TYPICAL_REQUEST.partition(b"\r\n\r\n")[0]
LARGE_HEADER_BLOCK = LARGE_HEADER_REQUEST.partition(b"\r\n\r\n")[0]

LIMITS = {
    "limit_request_line": 4094,
    "limit_request_fields": 100,
    "limit_request_field_size": 8190,
}

# 64 chunks of 1 KiB, fully buffered: recv() is never called.
CHUNKED_BODY = (
    b"".join(b"400\r\n" + b"z" * 1024 + b"\r\n" for _ in range(64)) + b"0\r\n\r\n"
)
CHUNKED_SLICES = [CHUNKED_BODY[i:i + 512] for i in range(0, len(CHUNKED_BODY), 512)]

CONTENT_LENGTH_BODY = b"y" * (64 * 1024)

RESPONSE_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Server": "asteri/3.0.0",
    "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
    "Cache-Control": "no-store",
    "X-Request-Id": "0f9c2d1a-7b3e-4f10-9a2c-6d5b8e1f4a77",
    "Connection": "keep-alive",
}
RESPONSE_BODY = b'{"status":"ok","items":[]}' * 64

HTTP2_PREFACE = HTTP2Handler.PREFACE + b"\x00\x00\x12\x04\x00\x00\x00\x00\x00"


def _no_recv():
    return b""


@contextmanager
def _pure_python_parser():
    """Force the pure-Python parsing path (used when the C extension is absent)."""
    original = http_mod.FAST_PARSER_AVAILABLE
    http_mod.FAST_PARSER_AVAILABLE = False
    try:
        yield
    finally:
        http_mod.FAST_PARSER_AVAILABLE = original


def test_parse_small_request(benchmark):
    benchmark(HTTPParser.parse, SMALL_REQUEST)


def test_parse_typical_request(benchmark):
    benchmark(HTTPParser.parse, TYPICAL_REQUEST)


def test_parse_typical_request_pure_python(benchmark):
    with _pure_python_parser():
        benchmark(HTTPParser.parse, TYPICAL_REQUEST)


def test_parse_post_request_with_body(benchmark):
    benchmark(HTTPParser.parse, POST_REQUEST)


def test_parse_large_header_request(benchmark):
    benchmark(HTTPParser.parse, LARGE_HEADER_REQUEST)


def test_parse_large_header_request_pure_python(benchmark):
    with _pure_python_parser():
        benchmark(HTTPParser.parse, LARGE_HEADER_REQUEST)


def test_header_dict_typical(benchmark):
    benchmark(header_dict, HEADER_BLOCK)


def test_header_dict_large_with_limits(benchmark):
    benchmark(header_dict, LARGE_HEADER_BLOCK, LIMITS)


def test_validate_header_block(benchmark):
    benchmark(validate_header_block, LARGE_HEADER_BLOCK, LIMITS)


def test_sanitize_header_name(benchmark):
    benchmark(sanitize_header_name, "X-Forwarded-For: 203.0.113.42")


def test_build_http_response(benchmark):
    def run():
        return build_http_response(200, dict(RESPONSE_HEADERS), RESPONSE_BODY)

    benchmark(run)


def test_build_error_response(benchmark):
    benchmark(build_error_response, 413)


def test_read_chunked_body_buffered(benchmark):
    benchmark(read_chunked_body, _no_recv, CHUNKED_BODY, 0)


def test_read_chunked_body_streaming(benchmark):
    def run():
        stream = iter(CHUNKED_SLICES)

        def recv():
            return next(stream, b"")

        return read_chunked_body(recv, b"", 0)

    benchmark(run)


def test_read_content_length_body(benchmark):
    benchmark(read_content_length_body, _no_recv, CONTENT_LENGTH_BODY, 64 * 1024)


def test_chunked_encode_small(benchmark):
    benchmark(chunked_encode_part, b"x" * 256)


def test_chunked_encode_large(benchmark):
    benchmark(chunked_encode_part, b"x" * (64 * 1024))


def test_is_http2_preface(benchmark):
    benchmark(HTTP2Handler.is_http2, HTTP2_PREFACE)
