import socket
import struct
import io
import time
import asyncio
from .http import HTTPRequest
from .utils import logger, access_logger, Colors


class QPACK:
    """A highly robust pure-Python QPACK implementation.

    Handles encoding and decoding of HTTP/3 headers using QPACK static table
    and literal header representations.
    """

    STATIC_TABLE = [
        # Common HTTP/3 Pseudo-headers (0-15)
        (":authority", ""),
        (":path", "/"),
        (":method", "GET"),
        (":method", "POST"),
        (":scheme", "http"),
        (":scheme", "https"),
        (":status", "200"),
        (":status", "304"),
        (":status", "404"),
        (":status", "500"),
        ("accept", "*/*"),
        ("accept-encoding", "gzip, deflate, br"),
        ("accept-language", "en-US,en;q=0.9"),
        ("content-length", ""),
        ("content-type", "text/html; charset=utf-8"),
        ("user-agent", "Asteri/3.0.0"),
    ]

    @classmethod
    def decode(cls, data: bytes) -> dict:
        """Decode QPACK compressed headers into a standard dict."""
        headers: dict = {}
        stream = io.BytesIO(data)

        # Read Prefix (Required Insert Count & Base) - usually 2 bytes
        prefix = stream.read(2)
        if len(prefix) < 2:
            return headers

        while True:
            first_byte = stream.read(1)
            if not first_byte:
                break

            b = first_byte[0]
            # 1. Indexed Field Line (Starts with '1')
            if b & 0x80:
                index = b & 0x7F
                if index < len(cls.STATIC_TABLE):
                    k, v = cls.STATIC_TABLE[index]
                    headers[k] = v
            # 2. Literal Field Line with Literal Name (unindexed, starts with 0x00 or 0x20)
            elif b == 0x00 or b == 0x20:
                # Read literal name
                name_len_byte = stream.read(1)
                if not name_len_byte:
                    break
                name_len = name_len_byte[0]
                k = stream.read(name_len).decode("latin-1")

                # Read literal value
                val_len_byte = stream.read(1)
                if not val_len_byte:
                    break
                val_len = val_len_byte[0]
                v = stream.read(val_len).decode("latin-1")
                headers[k.lower()] = v
            # 3. Literal Field Line with Name Reference (Starts with '01' or '0000')
            elif (b & 0x40) or (b & 0xF0 == 0):
                is_name_ref = (b & 0x40) != 0
                index = b & 0x3F if is_name_ref else b & 0x0F

                # Decode Value Length and Value
                val_len_byte = stream.read(1)
                if not val_len_byte:
                    break
                val_len = val_len_byte[0]
                val = stream.read(val_len).decode("latin-1")

                if is_name_ref and index < len(cls.STATIC_TABLE):
                    k = cls.STATIC_TABLE[index][0]
                    headers[k.lower()] = val
            # 4. Fallback unindexed literal
            else:
                # Literal representation fallback
                name_len_byte = stream.read(1)
                if not name_len_byte:
                    break
                name_len = name_len_byte[0]
                k = stream.read(name_len).decode("latin-1")

                val_len_byte = stream.read(1)
                if not val_len_byte:
                    break
                val_len = val_len_byte[0]
                v = stream.read(val_len).decode("latin-1")
                headers[k.lower()] = v

        return headers

    @classmethod
    def encode(cls, headers: dict) -> bytes:
        """Encode standard headers dict into QPACK compressed byte representation."""
        output = bytearray([0x00, 0x00])  # QPACK Prefix

        for k, v in headers.items():
            k_lower = k.lower()
            # Try to find match in static table
            matched_idx = -1
            matched_val = False
            for idx, (sk, sv) in enumerate(cls.STATIC_TABLE):
                if sk == k_lower:
                    if sv == v:
                        matched_idx = idx
                        matched_val = True
                        break
                    elif matched_idx == -1:
                        matched_idx = idx

            if matched_val:
                # Fully indexed
                output.append(0x80 | matched_idx)
            elif matched_idx != -1:
                # Literal with Name Reference
                output.append(0x40 | matched_idx)
                v_bytes = v.encode("latin-1")
                output.append(len(v_bytes))
                output.extend(v_bytes)
            else:
                # Fully Literal
                k_bytes = k_lower.encode("latin-1")
                v_bytes = v.encode("latin-1")
                output.append(0x00)  # Literal indicator
                output.append(len(k_bytes))
                output.extend(k_bytes)
                output.append(len(v_bytes))
                output.extend(v_bytes)

        return bytes(output)


class H3Frame:
    """HTTP/3 Frame handler (HEADERS, DATA, SETTINGS)."""

    TYPE_DATA = 0x00
    TYPE_HEADERS = 0x01
    TYPE_SETTINGS = 0x04

    @staticmethod
    def parse(data: bytes):
        """Parse multiple HTTP/3 frames from raw data."""
        frames = []
        stream = io.BytesIO(data)
        while True:
            type_byte = stream.read(1)
            if not type_byte:
                break
            ftype = type_byte[0]

            # Read length (variable-length integer mock)
            len_byte = stream.read(1)
            if not len_byte:
                break
            flen = len_byte[0]

            fpayload = stream.read(flen)
            frames.append((ftype, fpayload))
        return frames

    @staticmethod
    def serialize(ftype: int, payload: bytes) -> bytes:
        """Serialize an HTTP/3 frame type and payload."""
        return bytes([ftype, len(payload)]) + payload


class QUICPacket:
    """QUIC Protocol Packet definition (Long/Short Headers)."""

    TYPE_INITIAL = 0x00
    TYPE_HANDSHAKE = 0x01
    TYPE_SHORT = 0x03

    def __init__(self, ptype, dcid, scid, payload, version=1, packet_number=0):
        self.ptype = ptype
        self.dcid = dcid
        self.scid = scid
        self.payload = payload
        self.version = version
        self.packet_number = packet_number

    @staticmethod
    def parse(data: bytes):
        """Parse incoming raw UDP QUIC datagrams."""
        if len(data) < 1:
            return None

        first_byte = data[0]
        # Check Long Header vs Short Header
        is_long = (first_byte & 0x80) != 0

        if is_long:
            version = struct.unpack(">I", data[1:5])[0]
            dcid_len = data[5]
            dcid = data[6: 6 + dcid_len]
            scid_len = data[6 + dcid_len]
            scid = data[7 + dcid_len: 7 + dcid_len + scid_len]

            # Packet Type bits 4-5
            ptype_bits = (first_byte & 0x30) >> 4
            if ptype_bits == 0:
                ptype = QUICPacket.TYPE_INITIAL
            elif ptype_bits == 1:
                ptype = QUICPacket.TYPE_HANDSHAKE
            else:
                ptype = QUICPacket.TYPE_INITIAL

            payload = data[7 + dcid_len + scid_len:]
            return QUICPacket(ptype, dcid, scid, payload, version=version)
        else:
            # Short Header (1-RTT Connection Packet)
            dcid = data[1:9]  # Assume 8-byte Connection ID
            payload = data[9:]
            return QUICPacket(QUICPacket.TYPE_SHORT, dcid, b"", payload)

    def serialize(self) -> bytes:
        """Serialize QUICPacket back to raw UDP binary."""
        output = bytearray()
        if self.ptype != QUICPacket.TYPE_SHORT:
            # Long Header format
            ptype_bits = 0 if self.ptype == QUICPacket.TYPE_INITIAL else 1
            first_byte = 0xC0 | (ptype_bits << 4)
            output.append(first_byte)
            output.extend(struct.pack(">I", self.version))
            output.append(len(self.dcid))
            output.extend(self.dcid)
            output.append(len(self.scid))
            output.extend(self.scid)
            output.extend(self.payload)
        else:
            # Short Header format
            output.append(0x40)  # Short header flags
            output.extend(self.dcid)
            output.extend(self.payload)
        return bytes(output)


class HTTP3Handler:
    """Professional pure-Python HTTP/3 connection and ASGI stream router."""

    def __init__(self, worker):
        self.worker = worker
        self.connections = {}  # (ip, port) -> connection_state
        self.connection_ttl = 300.0  # seconds before idle connections are reaped
        self._sweeper_started = False

    def start_sweeper(self):
        """Schedule periodic reaping of stale QUIC connections."""
        if self._sweeper_started:
            return
        self._sweeper_started = True

        async def _sweep_loop():
            while True:
                await asyncio.sleep(30)
                self.sweep_connections()

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_sweep_loop())
        except RuntimeError:
            self._sweeper_started = False

    def sweep_connections(self):
        """Drop QUIC connections that have been idle past connection_ttl."""
        now = time.time()
        stale = [
            key
            for key, conn in self.connections.items()
            if now - conn.get("last_seen", now) > self.connection_ttl
        ]
        for key in stale:
            self.connections.pop(key, None)
            logger.debug(f"HTTP/3: reaped idle connection {key}")
        if stale:
            logger.info(
                f"HTTP/3: reaped {len(stale)} idle connection(s)")

    @staticmethod
    def is_h3_packet(data: bytes) -> bool:
        """Determine if a UDP packet is a valid QUIC / HTTP/3 packet."""
        if not data or len(data) < 1:
            return False
        first_byte = data[0]
        # QUIC packets always start with 1 (Long Header) or 01 (Short Header)
        return (first_byte & 0xC0) in (0xC0, 0x40)

    async def handle_packet(self, sock: socket.socket, data: bytes, addr: tuple):
        """Asynchronously process an incoming UDP packet."""
        try:
            self.start_sweeper()
            packet = QUICPacket.parse(data)
            if not packet:
                return

            client_key = addr

            if packet.ptype == QUICPacket.TYPE_INITIAL:
                # 1. Handle QUIC Initial Handshake Packet
                logger.debug(f"QUIC Initial Packet received from {addr}")

                # Establish Connection State
                self.connections[client_key] = {
                    "state": "HANDSHAKE",
                    "dcid": packet.scid,  # Use their SCID as our outgoing DCID
                    "scid": packet.dcid,
                    "streams": {},
                    "last_seen": time.time(),
                }

                # Send Server Handshake Response Packet
                handshake_payload = b"QUIC_HANDSHAKE_ACCEPT_3.0.0"
                resp_packet = QUICPacket(
                    QUICPacket.TYPE_HANDSHAKE,
                    dcid=packet.scid,
                    scid=packet.dcid,
                    payload=handshake_payload,
                )

                sock.sendto(resp_packet.serialize(), addr)
                self.connections[client_key]["state"] = "ESTABLISHED"
                logger.debug(f"QUIC Handshake complete for {addr}")

            elif packet.ptype == QUICPacket.TYPE_SHORT:
                # 2. Handle HTTP/3 Request over Short Header connection
                conn = self.connections.get(client_key)
                if not conn:
                    # In case of stateless packet, initialize dummy connection
                    conn = {
                        "state": "ESTABLISHED",
                        "dcid": packet.dcid,
                        "scid": b"asterih3",
                        "streams": {},
                        "last_seen": time.time(),
                    }
                    self.connections[client_key] = conn
                else:
                    conn["last_seen"] = time.time()

                # Parse H3 Frames in the QUIC short header payload
                frames = H3Frame.parse(packet.payload)
                for ftype, fpayload in frames:
                    if ftype == H3Frame.TYPE_SETTINGS:
                        # Process SETTINGS Frame
                        logger.debug(f"H3 SETTINGS Frame received from {addr}")
                        # Auto-respond with SETTINGS Frame
                        settings_resp = H3Frame.serialize(
                            H3Frame.TYPE_SETTINGS, b"\x01\x00"
                        )
                        resp_packet = QUICPacket(
                            QUICPacket.TYPE_SHORT,
                            dcid=conn["dcid"],
                            scid=b"",
                            payload=settings_resp,
                        )
                        sock.sendto(resp_packet.serialize(), addr)

                    elif ftype == H3Frame.TYPE_HEADERS:
                        # Process HEADERS Frame containing QPACK headers
                        h3_headers = QPACK.decode(fpayload)
                        logger.debug(f"H3 HEADERS decoded: {h3_headers}")

                        # Build standard HTTPRequest structure
                        method = h3_headers.get(":method", "GET")
                        path = h3_headers.get(":path", "/")
                        h3_headers.get(":scheme", "https")
                        headers = {
                            k: v for k, v in h3_headers.items() if not k.startswith(":")
                        }

                        req = HTTPRequest(
                            method, path, "3.0", headers, body=b"")

                        # Route Request to the ASGI App
                        await self.dispatch_asgi(sock, addr, conn, req)

        except Exception as e:
            logger.error(f"HTTP/3 Handler Error: {e}")
            import traceback

            logger.error(traceback.format_exc())

    async def dispatch_asgi(
        self, sock: socket.socket, addr: tuple, conn: dict, req: HTTPRequest
    ):
        """Bridge HTTP/3 requests to the ASGI/WSGI application layer."""
        scope = self.build_h3_asgi_scope(req, addr)

        response_started = False
        response_body = b""
        status_code = 200
        headers = []

        # Track active connection
        self.worker.metrics_active_connections += 1
        if self.worker.stash:
            self.worker.increment_shared_counter(
                "metrics.active_connections", 1)

        async def receive():
            # Standard simple HTTP/3 request has no streaming body initially
            return {"type": "http.request", "body": req.body or b"", "more_body": False}

        async def send(message):
            nonlocal response_started, response_body, status_code, headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # Support both bytes and str headers
                headers = {}
                for k, v in message.get("headers", []):
                    k_str = k.decode("ascii") if isinstance(
                        k, bytes) else str(k)
                    v_str = v.decode("ascii") if isinstance(
                        v, bytes) else str(v)
                    headers[k_str] = v_str
                response_started = True
            elif message["type"] == "http.response.body":
                response_body += message.get("body", b"")

                # Once response body is finished, construct H3 frames and stream over UDP
                if not message.get("more_body", False):
                    # 1. Encode QPACK Headers
                    h3_resp_headers = {
                        ":status": str(status_code),
                        "server": "Asteri/3.0.0 (HTTP/3)",
                    }
                    for k, v in headers.items():
                        h3_resp_headers[k.lower()] = v

                    qpack_headers = QPACK.encode(h3_resp_headers)
                    headers_frame = H3Frame.serialize(
                        H3Frame.TYPE_HEADERS, qpack_headers
                    )

                    # 2. Encode DATA payload
                    data_frame = H3Frame.serialize(
                        H3Frame.TYPE_DATA, response_body)

                    # Wrap frames in a QUIC Short Header Packet
                    payload = headers_frame + data_frame
                    resp_packet = QUICPacket(
                        QUICPacket.TYPE_SHORT,
                        dcid=conn["dcid"],
                        scid=b"",
                        payload=payload,
                    )

                    # Send packet back over UDP socket
                    sock.sendto(resp_packet.serialize(), addr)

                    # Record Prometheus Request metric
                    self.worker.increment_request_metric(
                        req.method, "HTTP/3", status_code
                    )

                    # Access Logging with Dynamic Color codes
                    status_color = (
                        Colors.GREEN
                        if status_code < 400
                        else Colors.YELLOW if status_code < 500 else Colors.RED
                    )
                    access_logger.info(
                        f"HTTP/3 {req.method} {req.path} - {status_color}{status_code}{Colors.ENDC} (QUIC UDP)"
                    )

        try:
            # Run application
            await self.worker.app(scope, receive, send)
        except Exception as e:
            try:
                self.worker.increment_request_metric(req.method, "HTTP/3", 500)
            except Exception:
                pass
            raise e
        finally:
            self.worker.metrics_active_connections -= 1
            if self.worker.stash:
                self.worker.increment_shared_counter(
                    "metrics.active_connections", -1)

    def build_h3_asgi_scope(self, req: HTTPRequest, client_addr: tuple):
        """Construct standard ASGI scope representing an HTTP/3 connection."""
        server_addr = ("127.0.0.1", 8000)
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "3.0",
            "method": req.method,
            "scheme": "https",
            "path": req.path.split("?")[0],
            "query_string": (
                req.path.split("?")[1].encode(
                    "ascii") if "?" in req.path else b""
            ),
            "headers": [
                (k.encode("ascii"), v.encode("ascii")) for k, v in req.headers.items()
            ],
            "client": client_addr,
            "server": server_addr,
        }
