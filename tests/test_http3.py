import unittest
from unittest.mock import MagicMock
from asteri.http3 import QPACK, H3Frame, QUICPacket, HTTP3Handler


class TestQPACK(unittest.TestCase):
    def test_encode_decode_static_table(self):
        """Test encoding and decoding utilizing static table indices."""
        headers = {
            ":method": "GET",
            ":path": "/index.html",
            ":scheme": "https",
            ":status": "200",
        }
        encoded = QPACK.encode(headers)
        decoded = QPACK.decode(encoded)

        self.assertEqual(decoded.get(":method"), "GET")
        self.assertEqual(decoded.get(":path"), "/index.html")
        self.assertEqual(decoded.get(":scheme"), "https")
        self.assertEqual(decoded.get(":status"), "200")

    def test_encode_decode_literals(self):
        """Test encoding and decoding of unindexed literal headers."""
        headers = {
            "x-custom-header": "my-custom-value",
            "content-type": "application/json",
            "server": "asteri",
        }
        encoded = QPACK.encode(headers)
        decoded = QPACK.decode(encoded)

        self.assertEqual(decoded.get("x-custom-header"), "my-custom-value")
        self.assertEqual(decoded.get("content-type"), "application/json")
        self.assertEqual(decoded.get("server"), "asteri")


class TestH3Frame(unittest.TestCase):
    def test_serialize_and_parse_frames(self):
        """Test round-trip serialization and parsing of HTTP/3 frames."""
        payload1 = b"some headers data"
        payload2 = b"some body data"

        frame1 = H3Frame.serialize(H3Frame.TYPE_HEADERS, payload1)
        frame2 = H3Frame.serialize(H3Frame.TYPE_DATA, payload2)

        combined = frame1 + frame2
        parsed = H3Frame.parse(combined)

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0][0], H3Frame.TYPE_HEADERS)
        self.assertEqual(parsed[0][1], payload1)
        self.assertEqual(parsed[1][0], H3Frame.TYPE_DATA)
        self.assertEqual(parsed[1][1], payload2)


class TestQUICPacket(unittest.TestCase):
    def test_long_header_initial_packet(self):
        """Test round-trip parsing of QUIC Long Header Initial packet."""
        dcid = b"\x01\x02\x03\x04"
        scid = b"\x05\x06\x07\x08"
        payload = b"quic initial payload"

        pkt = QUICPacket(
            QUICPacket.TYPE_INITIAL, dcid=dcid, scid=scid, payload=payload, version=1
        )

        serialized = pkt.serialize()
        parsed = QUICPacket.parse(serialized)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.ptype, QUICPacket.TYPE_INITIAL)
        self.assertEqual(parsed.dcid, dcid)
        self.assertEqual(parsed.scid, scid)
        self.assertEqual(parsed.payload, payload)
        self.assertEqual(parsed.version, 1)

    def test_short_header_packet(self):
        """Test round-trip parsing of QUIC Short Header packet."""
        dcid = b"\x10\x20\x30\x40\x50\x60\x70\x80"
        payload = b"quic short header data payload"

        pkt = QUICPacket(QUICPacket.TYPE_SHORT, dcid=dcid,
                         scid=b"", payload=payload)

        serialized = pkt.serialize()
        parsed = QUICPacket.parse(serialized)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.ptype, QUICPacket.TYPE_SHORT)
        self.assertEqual(parsed.dcid, dcid)
        self.assertEqual(parsed.payload, payload)


class TestHTTP3Handler(unittest.IsolatedAsyncioTestCase):
    async def test_is_h3_packet(self):
        """Verify the is_h3_packet utility works for Long and Short headers."""
        self.assertTrue(
            HTTP3Handler.is_h3_packet(b"\xc0\x00\x00\x00\x01")
        )  # Long Header
        self.assertTrue(HTTP3Handler.is_h3_packet(
            b"\x40\x01\x02\x03"))  # Short Header
        self.assertFalse(
            HTTP3Handler.is_h3_packet(b"\x00\x00\x00\x00")
        )  # TCP/Raw zero data

    async def test_quic_handshake(self):
        """Verify QUIC Initial packet receives Handshake accept response."""
        mock_worker = MagicMock()
        handler = HTTP3Handler(mock_worker)

        mock_socket = MagicMock()
        addr = ("127.0.0.1", 12345)

        # Build Initial packet
        dcid = b"serverci"
        scid = b"clientci"
        initial_pkt = QUICPacket(
            QUICPacket.TYPE_INITIAL, dcid=dcid, scid=scid, payload=b""
        )

        await handler.handle_packet(mock_socket, initial_pkt.serialize(), addr)

        # Verify socket.sendto was called to send Handshake response
        self.assertTrue(mock_socket.sendto.called)
        sent_data, sent_addr = mock_socket.sendto.call_args[0]
        self.assertEqual(sent_addr, addr)

        # Verify sent packet is a Handshake packet responding to the client
        resp_pkt = QUICPacket.parse(sent_data)
        self.assertEqual(resp_pkt.ptype, QUICPacket.TYPE_HANDSHAKE)
        self.assertEqual(resp_pkt.dcid, scid)
        self.assertEqual(resp_pkt.scid, dcid)
        self.assertEqual(resp_pkt.payload, b"QUIC_HANDSHAKE_ACCEPT_3.0.0")

    async def test_h3_asgi_request_routing(self):
        """Verify end-to-end ASGI application request routing over HTTP/3 UDP."""

        # 1. Define mock ASGI app
        async def mock_asgi_app(scope, receive, send):
            self.assertEqual(scope["type"], "http")
            self.assertEqual(scope["http_version"], "3.0")
            self.assertEqual(scope["method"], "POST")
            self.assertEqual(scope["path"], "/submit")

            # Start response
            await send(
                {
                    "type": "http.response.start",
                    "status": 201,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"x-powered-by", b"asteri"),
                    ],
                }
            )
            # Send body
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"status":"success"}',
                    "more_body": False,
                }
            )

        mock_worker = MagicMock()
        mock_worker.app = mock_asgi_app

        handler = HTTP3Handler(mock_worker)

        # Pre-establish connection
        addr = ("127.0.0.1", 12345)
        handler.connections[addr] = {
            "state": "ESTABLISHED",
            "dcid": b"clientci",
            "scid": b"serverci",
            "streams": {},
        }

        mock_socket = MagicMock()

        # Create H3 request headers
        h3_headers = {
            ":method": "POST",
            ":path": "/submit",
            ":scheme": "https",
            "content-type": "application/json",
        }
        qpack_payload = QPACK.encode(h3_headers)
        headers_frame = H3Frame.serialize(H3Frame.TYPE_HEADERS, qpack_payload)

        # Wrap in a QUIC Short Header Packet
        request_pkt = QUICPacket(
            QUICPacket.TYPE_SHORT, dcid=b"serverci", scid=b"", payload=headers_frame
        )

        # Route packet
        await handler.handle_packet(mock_socket, request_pkt.serialize(), addr)

        # 2. Verify UDP Response datagram
        self.assertTrue(mock_socket.sendto.called)
        sent_data, sent_addr = mock_socket.sendto.call_args[0]
        self.assertEqual(sent_addr, addr)

        resp_pkt = QUICPacket.parse(sent_data)
        self.assertEqual(resp_pkt.ptype, QUICPacket.TYPE_SHORT)
        self.assertEqual(resp_pkt.dcid, b"clientci")

        # Parse H3 Frames in the response packet
        frames = H3Frame.parse(resp_pkt.payload)
        self.assertEqual(len(frames), 2)

        # Verify Headers Frame
        self.assertEqual(frames[0][0], H3Frame.TYPE_HEADERS)
        resp_headers = QPACK.decode(frames[0][1])
        self.assertEqual(resp_headers.get(":status"), "201")
        self.assertEqual(resp_headers.get("content-type"), "application/json")
        self.assertEqual(resp_headers.get("x-powered-by"), "asteri")

        # Verify Data Frame
        self.assertEqual(frames[1][0], H3Frame.TYPE_DATA)
        self.assertEqual(frames[1][1], b'{"status":"success"}')


if __name__ == "__main__":
    unittest.main()
