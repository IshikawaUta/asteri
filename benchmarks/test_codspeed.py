from asteri.http import HTTPParser, chunked_encode_part, header_dict, read_chunked_body
from asteri.utils import parse_websocket_frame

HTTP_HEADERS = (
    b"GET /path/to/resource?query=1 HTTP/1.1\r\n"
    b"Host: localhost:8080\r\n"
    b"User-Agent: asteri-benchmark/3.0\r\n"
    b"Accept: */*\r\n"
    b"Accept-Encoding: gzip, deflate, br\r\n"
    b"Content-Length: 0\r\n"
    b"Connection: keep-alive\r\n\r\n"
)

CHUNKED_BODY = b"3\r\nabc\r\n3\r\ndef\r\n3\r\nghi\r\n0\r\n\r\n"

WEBSOCKET_FRAME = b"\x81\x03abc"


def test_parse_http(benchmark):
    benchmark(HTTPParser.parse, HTTP_HEADERS)


def test_header_dict(benchmark):
    benchmark(header_dict, HTTP_HEADERS)


def test_read_chunked_body(benchmark):
    def recv(*args):
        return CHUNKED_BODY

    benchmark(read_chunked_body, recv, b"", 0)


def test_parse_websocket_frame(benchmark):
    benchmark(parse_websocket_frame, WEBSOCKET_FRAME)


def test_chunked_encode(benchmark):
    benchmark(chunked_encode_part, b"x" * 256)
