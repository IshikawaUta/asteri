import http.client
from .utils import logger

try:
    from asteri import fastparser  # type: ignore

    FAST_PARSER_AVAILABLE = True
except ImportError:
    FAST_PARSER_AVAILABLE = False


class HTTPRequest:
    def __init__(self, method, path, version, headers, body=None):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.body = body


class HTTPParser:
    @staticmethod
    def parse(raw_data):
        """Simple HTTP/1.1 parser with fast C fallback."""
        if FAST_PARSER_AVAILABLE:
            try:
                res = fastparser.parse_http(raw_data)
                if res is not None:
                    method, path, version, headers, body = res
                    return HTTPRequest(method, path, version, headers, body)
            except Exception as e:
                logger.debug(f"C fastparser failed, falling back: {e}")

        try:
            # Split headers and body using bytes to keep body intact
            header_bytes, _, body = raw_data.partition(b"\r\n\r\n")

            # Decode headers using latin-1 (standard for HTTP)
            header_part = header_bytes.decode("latin-1")
            lines = header_part.split("\r\n")

            if not lines or not lines[0]:
                return None

            # Request line (e.g., "GET / HTTP/1.1")
            request_line = lines[0].split()
            if len(request_line) < 3:
                return None

            method, path, version = request_line

            # Headers
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.lower().strip()] = value.strip()

            return HTTPRequest(method, path, version, headers, body)
        except Exception as e:
            logger.error(f"Failed to parse HTTP request: {e}")
            return None

    @staticmethod
    def parse_raw(head, body, headers):
        """Build an HTTPRequest from an already validated request line and headers."""
        lines = head.split(b"\r\n") if isinstance(head, bytes) else head.split("\r\n")
        if not lines or not lines[0]:
            return None
        request_line = lines[0].split()
        if len(request_line) < 3:
            return None
        method = request_line[0].decode("latin-1") if isinstance(request_line[0], bytes) else request_line[0]
        path = request_line[1].decode("latin-1") if isinstance(request_line[1], bytes) else request_line[1]
        version = request_line[2].decode("latin-1") if isinstance(request_line[2], bytes) else request_line[2]
        return HTTPRequest(method, path, version, headers, body)


try:
    import h2.connection
    import h2.events
    import h2.config

    H2_AVAILABLE = True
except ImportError:
    H2_AVAILABLE = False


class HTTP2Handler:
    """Professional HTTP/2 handler using 'h2' library.

    Requests are dispatched to the configured ``request_handler`` callable
    with the signature ``(method, path, headers, body) -> (status, headers, body)``.
    """

    PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"

    @staticmethod
    def is_http2(data):
        return data.startswith(HTTP2Handler.PREFACE)

    @staticmethod
    def _default_handler(method, path, headers, body):
        return 200, [("content-type", "text/plain")], b"Hello from Asteri (HTTP/2)!"

    def __init__(self, sock, initial_data=None, request_handler=None,
                 max_body_size=0, max_concurrent_streams=100):
        self.sock = sock
        self.initial_data = initial_data
        self.request_handler = request_handler or HTTP2Handler._default_handler
        self.max_body_size = max_body_size or 0
        self.max_concurrent_streams = max_concurrent_streams or 100
        self.conn = None
        self.streams = {}

    def _setup_connection(self):
        if not H2_AVAILABLE:
            logger.error("HTTP/2 requested but 'h2' library is missing.")
            return False
        # Server side of the connection: do NOT initiate (clients do that).
        self.conn = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False))
        self.conn.local_settings.max_concurrent_streams = self.max_concurrent_streams
        self._send_all(self.conn.data_to_send())
        return True

    def _send_all(self, data):
        if data:
            self.sock.sendall(data)

    def handle(self):
        """Main HTTP/2 event loop for a connection."""
        if not self._setup_connection():
            return

        try:
            # Process initial data if provided
            if self.initial_data:
                self.process_data(self.initial_data)

            while True:
                data = self.sock.recv(65535)
                if not data:
                    break
                self.process_data(data)
        except Exception as e:
            logger.debug(f"H2 Connection closed: {e}")

    def process_data(self, data):
        try:
            events = self.conn.receive_data(data)
            for event in events:
                self._handle_event(event)
        except Exception as e:
            logger.error(f"H2 protocol error: {e}")
        self._send_all(self.conn.data_to_send())

    def _handle_event(self, event):
        if isinstance(event, h2.events.RequestReceived):
            self.streams[event.stream_id] = {
                "headers": {
                    k.decode("latin-1"): v.decode("latin-1")
                    for k, v in event.headers
                },
                "body": b"",
                "dispatched": False,
            }
            if event.stream_ended:
                self._dispatch(event.stream_id)
        elif isinstance(event, h2.events.DataReceived):
            stream = self.streams.get(event.stream_id)
            if stream is not None:
                stream["body"] += event.data
            # Always acknowledge to keep flow control windows updated
            self.conn.acknowledge_received_data(
                event.flow_controlled_length, event.stream_id
            )
            if event.stream_ended:
                self._dispatch(event.stream_id)
        elif isinstance(event, h2.events.StreamEnded):
            self._dispatch(event.stream_id)
        elif isinstance(event, h2.events.StreamReset):
            self.streams.pop(event.stream_id, None)

    def _dispatch(self, stream_id):
        stream = self.streams.pop(stream_id, None)
        if stream is None or stream["dispatched"]:
            return
        stream["dispatched"] = True

        headers = stream["headers"]
        method = headers.get(":method")
        path = headers.get(":path")
        if method is None or path is None:
            self._send_response(
                stream_id, 400, [("content-type", "text/plain")], b"Bad Request"
            )
            return

        app_headers = {k: v for k, v in headers.items() if not k.startswith(":")}
        if self.max_body_size:
            try:
                content_length = int(app_headers.get("content-length", "0") or 0)
                if content_length > self.max_body_size:
                    self._send_response(
                        stream_id, 413, [("content-type", "text/plain")],
                        ERROR_BODIES.get(413, b""),
                    )
                    return
            except ValueError:
                pass
        try:
            status, resp_headers, body = self.request_handler(
                method, path, app_headers, stream["body"]
            )
            self._send_response(stream_id, status, resp_headers, body)
        except Exception as e:
            logger.error(f"H2 application error: {e}")
            try:
                self._send_response(
                    stream_id,
                    500,
                    [("content-type", "text/plain")],
                    b"Internal Server Error",
                )
            except Exception:
                pass

    def _send_response(self, stream_id, status, headers, body):
        if body is None:
            body = b""
        elif isinstance(body, str):
            body = body.encode("utf-8")

        resp_headers = [(":status", sanitize_header_name(str(status)))]
        for k, v in headers:
            resp_headers.append(
                (
                    sanitize_header_name(k.encode("ascii") if isinstance(k, str) else k),
                    sanitize_header_name(v.encode("ascii") if isinstance(v, str) else v),
                )
            )

        self.conn.send_headers(stream_id, resp_headers, end_stream=not body)
        if body:
            self.conn.send_data(stream_id, body, end_stream=True)
        self._send_all(self.conn.data_to_send())


def sanitize_header_name(value):
    """Strip CR/LF from a header name/value to prevent response splitting."""
    if isinstance(value, bytes):
        return value.replace(b"\r", b" ").replace(b"\n", b" ")
    return value.replace("\r", " ").replace("\n", " ")


def build_http_response(status_code, headers, body):
    """Build a standard HTTP/1.1 response."""
    status_text = http.client.responses.get(status_code, "Unknown")

    # Ensure body is bytes for length calculation
    if isinstance(body, str):
        body = body.encode("utf-8")

    lines = [f"HTTP/1.1 {status_code} {status_text}"]

    if "Content-Length" not in headers and body:
        headers["Content-Length"] = str(len(body))

    for key, value in headers.items():
        safe_key = sanitize_header_name(key)
        safe_value = sanitize_header_name(value)
        lines.append(f"{safe_key}: {safe_value}")

    return "\r\n".join(lines).encode("latin-1") + b"\r\n\r\n" + body


class HTTPError(Exception):
    """Protocol-level HTTP error carrying a status code."""

    def __init__(self, status, message=None):
        self.status = status
        self.message = message or http.client.responses.get(status, "Error")
        super().__init__(f"HTTP {status} {self.message}")


ERROR_BODIES = {
    400: b"Bad Request",
    413: b"Request Entity Too Large",
    431: b"Request Header Fields Too Large",
    501: b"Not Implemented",
}


def build_error_response(status, message=None):
    """Build a minimal HTTP/1.1 error response."""
    if message is None:
        message = ERROR_BODIES.get(status, b"Error")
    if isinstance(message, str):
        message = message.encode("utf-8")
    return build_http_response(
        status, {"Content-Type": "text/plain"}, message
    )


def validate_header_block(header_bytes, limits):
    """Validate the HTTP header block against configured limits.

    Raises HTTPError(431) when a limit is exceeded. Limits keys:
    limit_request_line, limit_request_fields, limit_request_field_size.
    """
    lines = header_bytes.split(b"\r\n")
    if not lines or not lines[0]:
        raise HTTPError(400, "Empty request line")

    max_line = limits.get("limit_request_line", 4094)
    max_fields = limits.get("limit_request_fields", 100)
    max_field = limits.get("limit_request_field_size", 8190)

    if len(lines[0]) > max_line:
        raise HTTPError(431)
    header_lines = lines[1:]
    if len(header_lines) > max_fields:
        raise HTTPError(431)
    for header_line in header_lines:
        if header_line and len(header_line) > max_field:
            raise HTTPError(431)
    return True


def header_dict(header_bytes, limits=None):
    """Parse a raw header block (after the request line) into a str dict.

    When ``limits`` is provided the block is validated in the same single pass,
    raising HTTPError(431) for oversized request lines / fields / counts and
    HTTPError(400) for an empty block, matching ``validate_header_block``.
    """
    lines = header_bytes.split(b"\r\n")
    if not lines or not lines[0]:
        raise HTTPError(400, "Empty request line")

    max_line = limits.get("limit_request_line", 4094) if limits else 0
    max_fields = limits.get("limit_request_fields", 100) if limits else 0
    max_field = limits.get("limit_request_field_size", 8190) if limits else 0

    headers = {}
    first = True
    for line in lines:
        if first:
            first = False
            if max_line and len(line) > max_line:
                raise HTTPError(431)
            continue
        if not line:
            continue
        if max_field and len(line) > max_field:
            raise HTTPError(431)
        if b":" in line:
            key, value = line.split(b":", 1)
            headers[key.strip().lower().decode("latin-1")] = value.strip().decode(
                "latin-1"
            )
    if max_fields and len(headers) > max_fields:
        raise HTTPError(431)
    return headers


def _recv_until(recv, buffer, marker):
    """Keep reading until marker found; returns (buffer, idx) or raises HTTPError."""
    idx = buffer.find(marker)
    while idx == -1:
        chunk = recv()
        if not chunk:
            raise HTTPError(400, "Malformed chunked body")
        buffer += chunk
        idx = buffer.find(marker)
    return buffer, idx


def read_chunked_body(recv, initial, max_size=0):
    """Decode a Transfer-Encoding: chunked body from the socket.

    ``recv`` takes no arguments and returns a bytes chunk (or b"" on EOF).
    Returns (decoded_body, leftover_bytes). Raises HTTPError on malformed input
    or when max_size is exceeded.
    """
    buffer = initial
    out = bytearray()

    while True:
        buffer, idx = _recv_until(recv, buffer, b"\r\n")
        size_hex = buffer[:idx].split(b";")[0].strip()
        buffer = buffer[idx + 2:]
        try:
            size = int(size_hex, 16)
        except ValueError:
            raise HTTPError(400, "Invalid chunk size")

        if size == 0:
            # Consume trailers (or an immediate blank line = end of body)
            while True:
                buffer, ti = _recv_until(recv, buffer, b"\r\n")
                if buffer[:ti] == b"":
                    return bytes(out), buffer[ti + 2:]
                buffer = buffer[ti + 2:]

        while len(buffer) < size:
            chunk = recv()
            if not chunk:
                raise HTTPError(400, "Incomplete chunked body")
            buffer += chunk

        out += buffer[:size]
        if max_size and len(out) > max_size:
            raise HTTPError(413, "Request body too large")
        buffer = buffer[size:]

        # Consume the CRLF that terminates each data chunk
        if len(buffer) < 2:
            buffer, _ = _recv_until(recv, buffer, b"\r\n")
        if buffer[:2] != b"\r\n":
            raise HTTPError(400, "Malformed chunked body")
        buffer = buffer[2:]


def read_content_length_body(recv, initial, total, max_size=0):
    """Read exactly ``total`` body bytes (content-length semantics).

    ``initial`` already holds some body bytes.
    Returns (body_bytes, leftover_bytes).
    """
    if max_size and total > max_size:
        raise HTTPError(413, "Request body too large")

    got = len(initial)
    if got >= total:
        return initial[:total], initial[total:]

    body = bytearray(initial)
    leftover = bytearray()
    while got < total:
        chunk = recv()
        if not chunk:
            break
        need = total - got
        body += chunk[:need]
        leftover += chunk[need:]
        got = min(total, got + len(chunk))
    return bytes(body[:total]), bytes(leftover)


def chunked_encode_part(body):
    """Encode one data chunk per HTTP/1.1 chunked transfer coding."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not body:
        return b""
    return b"%x\r\n%s\r\n" % (len(body), body)


def chunked_terminator():
    return b"0\r\n\r\n"
