"""Benchmarks for the auxiliary wire protocols handled by Asteri.

uWSGI packets, HAProxy PROXY protocol headers, WebSocket frames, the Stash
TLV codec and the Dirty multi-app router.
"""

import struct

from asteri.dirty import TLV, DirtyAppLoader
from asteri.utils import (
    make_websocket_frame,
    parse_proxy_protocol,
    parse_websocket_frame,
)
from asteri.uwsgi import UWSGIHandler

# --------------------------------------------------------------------------
# uWSGI binary protocol
# --------------------------------------------------------------------------
UWSGI_VARS = {
    "REQUEST_METHOD": "GET",
    "REQUEST_URI": "/api/v1/users?page=3&per_page=50",
    "PATH_INFO": "/api/v1/users",
    "QUERY_STRING": "page=3&per_page=50",
    "SERVER_PROTOCOL": "HTTP/1.1",
    "SERVER_NAME": "api.example.com",
    "SERVER_PORT": "443",
    "REMOTE_ADDR": "203.0.113.42",
    "HTTP_HOST": "api.example.com",
    "HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0",
    "HTTP_ACCEPT": "text/html,application/xhtml+xml,application/xml;q=0.9",
    "HTTP_ACCEPT_ENCODING": "gzip, deflate, br",
    "HTTP_COOKIE": "session=8f14e45fceea167a5a36dedd4bea2543; theme=dark",
    "HTTP_X_FORWARDED_FOR": "203.0.113.42, 198.51.100.7",
}


def _build_uwsgi_packet(variables):
    var_data = b""
    for key, value in variables.items():
        kb = key.encode("latin-1")
        vb = value.encode("latin-1")
        var_data += struct.pack("<H", len(kb)) + kb
        var_data += struct.pack("<H", len(vb)) + vb
    return struct.pack("<BHB", 0, len(var_data), 0) + var_data


UWSGI_PACKET = _build_uwsgi_packet(UWSGI_VARS)

# --------------------------------------------------------------------------
# PROXY protocol
# --------------------------------------------------------------------------
PROXY_V1 = b"PROXY TCP4 203.0.113.42 198.51.100.7 56324 443\r\nGET / HTTP/1.1\r\n\r\n"

_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"
_V2_IPV4_ADDR = (
    bytes([203, 0, 113, 42])
    + bytes([198, 51, 100, 7])
    + (56324).to_bytes(2, "big")
    + (443).to_bytes(2, "big")
)
PROXY_V2_IPV4 = (
    _V2_SIGNATURE
    + b"\x21\x11"
    + len(_V2_IPV4_ADDR).to_bytes(2, "big")
    + _V2_IPV4_ADDR
    + b"GET / HTTP/1.1\r\n\r\n"
)

_V2_IPV6_ADDR = (
    bytes.fromhex("20010db8000000000000000000000001")
    + bytes.fromhex("20010db8000000000000000000000002")
    + (56324).to_bytes(2, "big")
    + (443).to_bytes(2, "big")
)
PROXY_V2_IPV6 = (
    _V2_SIGNATURE
    + b"\x21\x21"
    + len(_V2_IPV6_ADDR).to_bytes(2, "big")
    + _V2_IPV6_ADDR
    + b"GET / HTTP/1.1\r\n\r\n"
)

NO_PROXY_HEADER = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"

# --------------------------------------------------------------------------
# WebSocket frames
# --------------------------------------------------------------------------
SMALL_PAYLOAD = b"ping"
MEDIUM_PAYLOAD = b'{"type":"update","seq":42,"data":"' + b"m" * 900 + b'"}'
LARGE_PAYLOAD = b"L" * (16 * 1024)


def _mask(payload, mask_key=b"\x37\xfa\x21\x3d"):
    return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))


def _client_frame(payload, opcode=1):
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length <= 125:
        header.append(0x80 | length)
    elif length <= 65535:
        header.append(0x80 | 126)
        header.extend(length.to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(length.to_bytes(8, "big"))
    header.extend(b"\x37\xfa\x21\x3d")
    return bytes(header) + _mask(payload)


SMALL_FRAME = _client_frame(SMALL_PAYLOAD)
MEDIUM_FRAME = _client_frame(MEDIUM_PAYLOAD)
LARGE_FRAME = _client_frame(LARGE_PAYLOAD)

# --------------------------------------------------------------------------
# Stash TLV codec
# --------------------------------------------------------------------------
TLV_VALUE = b"v" * 4096
TLV_PACKET = TLV.encode(1, TLV_VALUE)

# --------------------------------------------------------------------------
# Dirty multi-app router
# --------------------------------------------------------------------------
ROUTER = DirtyAppLoader(
    "api.example.com=app_api:app,"
    "admin.example.com=app_admin:app,"
    "cdn.example.com=app_cdn:app,"
    "/api/v1=app_v1:app,"
    "/api/v2=app_v2:app,"
    "/static=app_static:app,"
    "default=app_default:app"
)


def test_uwsgi_parse(benchmark):
    benchmark(UWSGIHandler.parse, UWSGI_PACKET)


def test_uwsgi_is_uwsgi(benchmark):
    benchmark(UWSGIHandler.is_uwsgi, UWSGI_PACKET)


def test_proxy_protocol_v1(benchmark):
    benchmark(parse_proxy_protocol, PROXY_V1)


def test_proxy_protocol_v2_ipv4(benchmark):
    benchmark(parse_proxy_protocol, PROXY_V2_IPV4)


def test_proxy_protocol_v2_ipv6(benchmark):
    benchmark(parse_proxy_protocol, PROXY_V2_IPV6)


def test_proxy_protocol_absent(benchmark):
    benchmark(parse_proxy_protocol, NO_PROXY_HEADER)


def test_websocket_parse_small_frame(benchmark):
    benchmark(parse_websocket_frame, SMALL_FRAME)


def test_websocket_parse_medium_frame(benchmark):
    benchmark(parse_websocket_frame, MEDIUM_FRAME)


def test_websocket_parse_large_frame(benchmark):
    benchmark(parse_websocket_frame, LARGE_FRAME)


def test_websocket_make_medium_frame(benchmark):
    benchmark(make_websocket_frame, MEDIUM_PAYLOAD)


def test_websocket_make_large_frame(benchmark):
    benchmark(make_websocket_frame, LARGE_PAYLOAD)


def test_tlv_encode(benchmark):
    benchmark(TLV.encode, 1, TLV_VALUE)


def test_tlv_decode(benchmark):
    benchmark(TLV.decode, TLV_PACKET)


def test_dirty_router_match_host(benchmark):
    benchmark(ROUTER._match_app, "cdn.example.com:8443", "/assets/logo.svg")


def test_dirty_router_match_path(benchmark):
    benchmark(ROUTER._match_app, "unknown.example.com", "/api/v2/users")
