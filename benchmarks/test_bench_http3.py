"""HTTP/3 and QUIC benchmarks.

The HTTP/3 stack is pure Python, so QPACK header (de)compression, frame
framing and QUIC packet (de)serialization are directly on the request path.
"""

from asteri.http3 import QPACK, H3Frame, QUICPacket

REQUEST_HEADERS = {
    ":method": "GET",
    ":scheme": "https",
    ":authority": "api.example.com",
    ":path": "/api/v1/users?page=3&per_page=50",
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br",
    "user-agent": "Asteri/3.0.0",
    "x-request-id": "0f9c2d1a-7b3e-4f10-9a2c-6d5b8e1f4a77",
    "cookie": "session=8f14e45fceea167a5a36dedd4bea2543; theme=dark",
}

RESPONSE_HEADERS = {
    ":status": "200",
    "content-type": "text/html; charset=utf-8",
    "content-length": "4096",
    "server": "asteri/3.0.0",
    "x-frame-options": "DENY",
}

ENCODED_REQUEST_HEADERS = QPACK.encode(REQUEST_HEADERS)
ENCODED_RESPONSE_HEADERS = QPACK.encode(RESPONSE_HEADERS)

DATA_PAYLOAD = b"a" * 200
FRAME_STREAM = (
    H3Frame.serialize(H3Frame.TYPE_HEADERS, ENCODED_REQUEST_HEADERS)
    + H3Frame.serialize(H3Frame.TYPE_DATA, DATA_PAYLOAD)
    + H3Frame.serialize(H3Frame.TYPE_SETTINGS, b"\x01\x00\x06\x00")
) * 8

QUIC_PAYLOAD = b"q" * 1200
LONG_PACKET = QUICPacket(
    QUICPacket.TYPE_INITIAL,
    b"\x01\x02\x03\x04\x05\x06\x07\x08",
    b"\x11\x12\x13\x14",
    QUIC_PAYLOAD,
)
SHORT_PACKET = QUICPacket(
    QUICPacket.TYPE_SHORT, b"\x01\x02\x03\x04\x05\x06\x07\x08", b"", QUIC_PAYLOAD
)
LONG_PACKET_BYTES = LONG_PACKET.serialize()
SHORT_PACKET_BYTES = SHORT_PACKET.serialize()


def test_qpack_encode_request_headers(benchmark):
    benchmark(QPACK.encode, REQUEST_HEADERS)


def test_qpack_encode_response_headers(benchmark):
    benchmark(QPACK.encode, RESPONSE_HEADERS)


def test_qpack_decode_request_headers(benchmark):
    benchmark(QPACK.decode, ENCODED_REQUEST_HEADERS)


def test_qpack_decode_response_headers(benchmark):
    benchmark(QPACK.decode, ENCODED_RESPONSE_HEADERS)


def test_h3_frame_parse_stream(benchmark):
    benchmark(H3Frame.parse, FRAME_STREAM)


def test_h3_frame_serialize_data(benchmark):
    benchmark(H3Frame.serialize, H3Frame.TYPE_DATA, DATA_PAYLOAD)


def test_quic_parse_long_header(benchmark):
    benchmark(QUICPacket.parse, LONG_PACKET_BYTES)


def test_quic_parse_short_header(benchmark):
    benchmark(QUICPacket.parse, SHORT_PACKET_BYTES)


def test_quic_serialize_long_header(benchmark):
    benchmark(LONG_PACKET.serialize)


def test_quic_serialize_short_header(benchmark):
    benchmark(SHORT_PACKET.serialize)
