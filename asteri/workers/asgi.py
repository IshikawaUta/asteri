import asyncio
import socket
import ssl
import os
from .base import BaseWorker
from ..http import (
    build_http_response,
    build_error_response,
    HTTPError,
    HTTPParser,
    HTTP2Handler,
    sanitize_header_name,
    validate_header_block,
)
from ..utils import logger, access_logger, Colors


class ASGIWorker(BaseWorker):
    def run(self):
        # self.init_process() # Already called by Arbiter
        # Prefer uvloop when available (as Uvicorn does) for a large speedup;
        # fall back to the standard asyncio loop otherwise.
        try:
            import uvloop  # type: ignore[import-untyped]

            loop = uvloop.new_event_loop()
        except ImportError:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.main_loop())
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def handle_exit(self, sig, frame):
        """Quick shutdown: stop accepting so the event loop can exit promptly."""
        self.alive = False
        for sock in getattr(self, "sockets", []) or []:
            try:
                sock.close()
            except OSError:
                pass

    async def main_loop(self):
        tasks = []
        for sock in self.sockets:
            sock.setblocking(False)
            tasks.append(asyncio.create_task(self.accept_loop(sock)))

        await asyncio.gather(*tasks)

    async def accept_loop(self, sock):
        h3_handler = None
        if sock.type == socket.SOCK_DGRAM:
            from asteri.http3 import HTTP3Handler

            h3_handler = HTTP3Handler(self)

        loop = asyncio.get_running_loop()
        server_addr = None

        while self.alive:
            try:
                if not self.alive:
                    return
                if sock.type == socket.SOCK_STREAM:
                    try:
                        # Event-driven accept (as Uvicorn/libuv does) instead of
                        # polling + sleep: the loop wakes only when a connection
                        # arrives, avoiding 50ms stalls on an empty accept queue.
                        client, addr = await loop.sock_accept(sock)
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not self.alive:
                        try:
                            client.close()
                        except OSError:
                            pass
                        return
                    if server_addr is None:
                        try:
                            server_addr = sock.getsockname()
                        except OSError:
                            pass
                    asyncio.create_task(
                        self.handle_asgi_request(
                            client, listener_sock=sock, server_addr=server_addr
                        )
                    )
                elif sock.type == socket.SOCK_DGRAM:
                    import errno

                    try:
                        data, addr = sock.recvfrom(65535)
                        if data:
                            asyncio.create_task(
                                h3_handler.handle_packet(sock, data, addr)
                            )
                    except (BlockingIOError, OSError) as e:
                        if isinstance(e, OSError) and e.errno not in (
                            errno.EWOULDBLOCK,
                            errno.EAGAIN,
                        ):
                            raise
                        await asyncio.sleep(0.01)
            except Exception:
                if not self.alive:
                    return
                await asyncio.sleep(0.1)

    async def handle_asgi_status(self, sock):
        try:
            from ..utils import build_status_html

            status_html = build_status_html(
                self.__class__.__name__, os.getpid(), self.ppid
            )
            await asyncio.get_running_loop().sock_sendall(
                sock,
                build_http_response(
                    200, {"Content-Type": "text/html"}, status_html),
            )
            access_logger.info(
                f"GET /asteri-status - {Colors.GREEN}200{Colors.ENDC}")
        except Exception:
            pass

    async def handle_asgi_metrics(self, sock):
        try:
            metrics_text = self._cached_metrics()
            await asyncio.get_running_loop().sock_sendall(
                sock,
                build_http_response(
                    200,
                    {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
                    metrics_text,
                ),
            )
            access_logger.info(
                f"GET /metrics - {Colors.GREEN}200{Colors.ENDC}")
        except Exception:
            pass

    async def handle_asgi_request(self, sock, listener_sock=None, server_addr=None):
        loop = asyncio.get_running_loop()
        if not self.acquire_connection(sock):
            return
        try:
            data = await loop.sock_recv(sock, 4096)
            if not data:
                return

            proxy_client = None
            proxy_server = None
            if self.proxy_protocol:
                from asteri.utils import parse_proxy_protocol

                proxy_client, proxy_server, remaining = parse_proxy_protocol(data)
                data = remaining
                if not data:
                    data = await loop.sock_recv(sock, 4096)
                    if not data:
                        return

            # Internal Status Dashboard
            if not self.disable_dashboard and b"GET /asteri-status" in data:
                await self.handle_asgi_status(sock)
                return

            # Internal Prometheus Metrics Endpoint
            if b"GET /metrics" in data:
                await self.handle_asgi_metrics(sock)
                return

            # HTTP/2 (h2) protocol support
            if HTTP2Handler.is_http2(data):
                await self.handle_asgi_http2(sock, initial_data=data)
                return

            head = data.split(b"\r\n\r\n", 1)[0]
            try:
                validate_header_block(head, self.http_limits)
            except HTTPError as e:
                await loop.sock_sendall(sock, build_error_response(e.status))
                return

            req = HTTPParser.parse(data)
            if not req:
                return

            # Read / decode the full request body with limits enforced
            try:
                req = await self._read_request_body(sock, req, data)
            except HTTPError as e:
                await loop.sock_sendall(sock, build_error_response(e.status))
                return

            scope = self.build_asgi_scope(
                req, sock, listener_sock, proxy_client, proxy_server, server_addr
            )

            is_websocket = (
                req.headers.get("upgrade", "").lower() == "websocket"
                and "upgrade" in req.headers.get("connection", "").lower()
            )

            if is_websocket:
                await self.handle_asgi_websocket(sock, req, scope)
                return

            response_started = False
            response_body = b""
            status_code = 200
            headers = []

            async def receive():
                return {
                    "type": "http.request",
                    "body": req.body or b"",
                    "more_body": False,
                }

            async def send(message):
                nonlocal response_started, response_body, status_code, headers
                if message["type"] == "http.response.early_hints":
                    try:
                        hint_headers = message.get("headers", [])
                        hint_lines = ["HTTP/1.1 103 Early Hints"]
                        for k, v in hint_headers:
                            safe_k = sanitize_header_name(
                                k.decode("ascii") if isinstance(k, bytes) else str(k))
                            safe_v = sanitize_header_name(
                                v.decode("ascii") if isinstance(v, bytes) else str(v))
                            hint_lines.append(f"{safe_k}: {safe_v}")
                        hint_lines.append("\r\n")
                        await loop.sock_sendall(
                            sock, ("\r\n".join(hint_lines)).encode("latin-1")
                        )
                    except OSError:
                        pass
                elif message["type"] == "http.response.start":
                    status_code = message["status"]
                    headers = {}
                    for k, v in message.get("headers", []):
                        headers[
                            k.decode("ascii") if isinstance(k, bytes) else str(k)
                        ] = v.decode("ascii") if isinstance(v, bytes) else str(v)
                    response_started = True
                elif message["type"] == "http.response.body":
                    response_body += message.get("body", b"")
                    if not message.get("more_body", False):
                        await loop.sock_sendall(
                            sock,
                            build_http_response(
                                status_code, headers, response_body),
                        )
                        self.increment_request_metric(
                            req.method, "HTTP/1.1", status_code
                        )
                        status_color = (
                            Colors.GREEN
                            if status_code < 400
                            else Colors.YELLOW
                            if status_code < 500
                            else Colors.RED
                        )
                        access_logger.info(
                            f"{req.method} {req.path} - {status_color}{status_code}{Colors.ENDC}"
                        )

            await self.app(scope, receive, send)
        except Exception as e:
            logger.error(f"ASGI Error: {e}")
            try:
                if "req" in locals() and req:
                    self.increment_request_metric(req.method, "HTTP/1.1", 500)
            except Exception:
                pass
            import traceback

            logger.error(traceback.format_exc())
        finally:
            self.release_connection()
            try:
                sock.close()
            except OSError:
                pass

    async def _read_request_body(self, sock, req, data):
        """Read the full request body, decoding chunked or enforcing limits."""
        loop = asyncio.get_running_loop()
        te = req.headers.get("transfer-encoding", "").lower()

        _, _, body_initial = data.partition(b"\r\n\r\n")

        if te and "chunked" in te:
            buffer = body_initial
            out = bytearray()
            while True:
                while b"\r\n" not in buffer:
                    chunk = await loop.sock_recv(sock, 8192)
                    if not chunk:
                        raise HTTPError(400, "Incomplete chunked body")
                    buffer += chunk
                line, buffer = buffer.split(b"\r\n", 1)
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    raise HTTPError(400, "Invalid chunk size")
                if size == 0:
                    while b"\r\n" not in buffer:
                        chunk = await loop.sock_recv(sock, 8192)
                        if not chunk:
                            break
                        buffer += chunk
                    break
                while len(buffer) < size:
                    chunk = await loop.sock_recv(sock, 8192)
                    if not chunk:
                        raise HTTPError(400, "Incomplete chunked body")
                    buffer += chunk
                out += buffer[:size]
                if self.max_body_size and len(out) > self.max_body_size:
                    raise HTTPError(413, "Request body too large")
                buffer = buffer[size:]
                if len(buffer) < 2:
                    buffer += await loop.sock_recv(sock, 8192)
                buffer = buffer[2:]
            req.body = bytes(out)
            req.headers["content-length"] = str(len(out))
            req.headers.pop("transfer-encoding", None)
            return req

        cl_header = req.headers.get("content-length")
        if cl_header:
            try:
                total = int(cl_header.strip() or 0)
            except ValueError:
                raise HTTPError(400, "Invalid Content-Length")
            if total < 0:
                raise HTTPError(400, "Invalid Content-Length")
        else:
            total = 0

        if self.max_body_size and total > self.max_body_size:
            raise HTTPError(413, "Request body too large")

        body = bytearray(req.body or b"")
        while len(body) < total:
            chunk = await loop.sock_recv(sock, 8192)
            if not chunk:
                break
            body += chunk
            if self.max_body_size and len(body) > self.max_body_size:
                raise HTTPError(413, "Request body too large")
        req.body = bytes(body[:total])
        return req

    async def handle_asgi_websocket(self, sock, req, scope):
        scope["type"] = "websocket"
        scope["subprotocols"] = [
            sub.strip()
            for sub in req.headers.get("sec-websocket-protocol", "").split(",")
            if sub.strip()
        ]

        loop = asyncio.get_running_loop()

        import hashlib
        import base64

        ws_key = req.headers.get("sec-websocket-key", "")
        guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        ws_accept = base64.b64encode(
            hashlib.sha1((ws_key + guid).encode("utf-8")).digest()
        ).decode("utf-8")

        handshake_resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {ws_accept}\r\n\r\n"
        ).encode("utf-8")

        events = asyncio.Queue()
        await events.put({"type": "websocket.connect"})

        read_buffer = b""

        async def socket_reader():
            nonlocal read_buffer
            from asteri.utils import parse_websocket_frame

            try:
                while True:
                    chunk = await loop.sock_recv(sock, 4096)
                    if not chunk:
                        await events.put({"type": "websocket.disconnect", "code": 1006})
                        break
                    read_buffer += chunk

                    while True:
                        opcode, payload, remaining = parse_websocket_frame(
                            read_buffer)
                        if opcode is None:
                            break
                        read_buffer = remaining

                        if opcode == 8:
                            await events.put(
                                {"type": "websocket.disconnect", "code": 1000}
                            )
                            return
                        elif opcode == 9:
                            from asteri.utils import make_websocket_frame

                            await loop.sock_sendall(
                                sock, make_websocket_frame(payload, opcode=10)
                            )
                        elif opcode in (1, 2):
                            event = {"type": "websocket.receive"}
                            if opcode == 1:
                                event["text"] = payload.decode("utf-8")
                            else:
                                event["bytes"] = payload
                            await events.put(event)
            except Exception:
                await events.put({"type": "websocket.disconnect", "code": 1006})

        reader_task = asyncio.create_task(socket_reader())

        async def receive():
            return await events.get()

        async def send(message):
            from asteri.utils import make_websocket_frame

            msg_type = message.get("type")
            if msg_type == "websocket.accept":
                await loop.sock_sendall(sock, handshake_resp)
            elif msg_type == "websocket.send":
                text = message.get("text")
                binary = message.get("bytes")
                if text is not None:
                    await loop.sock_sendall(sock, make_websocket_frame(text, opcode=1))
                elif binary is not None:
                    await loop.sock_sendall(
                        sock, make_websocket_frame(binary, opcode=2)
                    )
            elif msg_type == "websocket.close":
                close_code = message.get("code", 1000)
                await loop.sock_sendall(
                    sock,
                    make_websocket_frame(
                        close_code.to_bytes(2, byteorder="big"), opcode=8
                    ),
                )

        try:
            await self.app(scope, receive, send)
        finally:
            reader_task.cancel()

    async def handle_asgi_http2(self, sock, initial_data=None):
        """Process an HTTP/2 connection and route requests to the ASGI app."""
        try:
            import h2.connection
            import h2.events
            import h2.config
        except ImportError:
            logger.error(
                "HTTP/2 requested but 'h2' library is missing on ASGI worker.")
            return

        loop = asyncio.get_running_loop()
        # Server side of the connection: do NOT initiate (clients do that).
        conn = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False))
        send_lock = asyncio.Lock()

        async def flush():
            out = conn.data_to_send()
            if out:
                async with send_lock:
                    await loop.sock_sendall(sock, out)

        await flush()

        streams = {}
        app_tasks = []

        def schedule_app(stream_id):
            stream = streams.pop(stream_id, None)
            if stream is not None and not stream["dispatched"]:
                stream["dispatched"] = True
                app_tasks.append(
                    asyncio.create_task(
                        self._run_h2_asgi_app(sock, conn, stream, send_lock)
                    )
                )

        async def process(data):
            try:
                events = conn.receive_data(data)
                for event in events:
                    if isinstance(event, h2.events.RequestReceived):
                        streams[event.stream_id] = {
                            "id": event.stream_id,
                            "headers": {
                                k.decode("latin-1"): v.decode("latin-1")
                                for k, v in event.headers
                            },
                            "body": b"",
                            "dispatched": False,
                        }
                        if event.stream_ended:
                            schedule_app(event.stream_id)
                    elif isinstance(event, h2.events.DataReceived):
                        stream = streams.get(event.stream_id)
                        if stream is not None:
                            stream["body"] += event.data
                        conn.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                        if event.stream_ended:
                            schedule_app(event.stream_id)
                    elif isinstance(event, h2.events.StreamEnded):
                        schedule_app(event.stream_id)
                    elif isinstance(event, h2.events.StreamReset):
                        streams.pop(event.stream_id, None)
            except Exception as e:
                logger.error(f"H2 protocol error: {e}")
            await flush()

        try:
            if initial_data:
                await process(initial_data)
            while True:
                data = await loop.sock_recv(sock, 65535)
                if not data:
                    break
                await process(data)
        finally:
            for task in app_tasks:
                task.cancel()
            try:
                conn.close_connection()
            except Exception:
                pass
            await flush()

    async def _run_h2_asgi_app(self, sock, conn, stream, send_lock):
        """Run a single HTTP/2 stream against the ASGI app and send its response."""
        loop = asyncio.get_running_loop()
        stream_id = stream["id"]
        h2_headers = stream["headers"]
        method = h2_headers.get(":method", "GET")
        path = h2_headers.get(":path", "/")
        req_headers = [
            (k.encode("ascii"), v.encode("ascii"))
            for k, v in h2_headers.items()
            if not k.startswith(":")
        ]
        body = stream["body"]

        if self.max_body_size and len(body) > self.max_body_size:
            response = {
                "status": 413,
                "headers": [(b"content-type", b"text/plain")],
                "body": b"Request Entity Too Large",
            }
            resp_headers = [(":status", "413")]
            for k, v in response["headers"]:
                resp_headers.append(
                    (
                        k if isinstance(k, bytes) else k.encode("ascii"),
                        v if isinstance(v, bytes) else v.encode("ascii"),
                    )
                )
            conn.send_headers(stream_id, resp_headers, end_stream=False)
            conn.send_data(stream_id, response["body"], end_stream=True)
            async with send_lock:
                await loop.sock_sendall(sock, conn.data_to_send())
            self.increment_request_metric(method, "HTTP/2", 413)
            return

        scope = self.build_h2_asgi_scope(method, path, req_headers, sock)

        response = {
            "status": 500,
            "headers": [(b"content-type", b"text/plain")],
            "body": b"",
        }
        body_consumed = False

        async def receive():
            nonlocal body_consumed
            if body_consumed:
                return {"type": "http.request", "body": b"", "more_body": False}
            body_consumed = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                response["headers"] = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                response["body"] += message.get("body", b"")

        try:
            await self.app(scope, receive, send)
        except Exception as e:
            logger.error(f"ASGI (HTTP/2) Error: {e}")
            response["status"] = 500
            response["headers"] = [(b"content-type", b"text/plain")]
            response["body"] = b"Internal Server Error"

        resp_headers = [(":status", str(response["status"]))]
        for k, v in response["headers"]:
            resp_headers.append(
                (
                    k if isinstance(k, bytes) else k.encode("ascii"),
                    v if isinstance(v, bytes) else v.encode("ascii"),
                )
            )

        conn.send_headers(stream_id, resp_headers, end_stream=not response["body"])
        if response["body"]:
            conn.send_data(stream_id, response["body"], end_stream=True)

        async with send_lock:
            await loop.sock_sendall(sock, conn.data_to_send())

        self.increment_request_metric(method, "HTTP/2", response["status"])
        status_color = (
            Colors.GREEN
            if response["status"] < 400
            else Colors.YELLOW if response["status"] < 500 else Colors.RED
        )
        access_logger.info(
            f"{method} {path} - {status_color}{response['status']}{Colors.ENDC}"
        )

    def build_h2_asgi_scope(self, method, path, headers, sock):
        """Construct a standard ASGI scope for an HTTP/2 request."""
        try:
            server_addr = sock.getsockname()
            client_addr = sock.getpeername()
        except Exception:
            server_addr = ("127.0.0.1", 8000)
            client_addr = ("127.0.0.1", 0)

        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "2.0",
            "method": method,
            "scheme": "https" if isinstance(sock, ssl.SSLSocket) else "http",
            "path": path.split("?")[0],
            "query_string": (
                path.split("?")[1].encode("ascii") if "?" in path else b""
            ),
            "headers": headers,
            "client": client_addr,
            "server": server_addr,
        }

    def build_asgi_scope(
        self, req, sock, listener_sock=None, proxy_client=None, proxy_server=None,
        server_addr=None,
    ):
        try:
            # Prefer listener_sock for server address, but fallback to client_sock's local address
            if server_addr is None:
                server_sock = listener_sock or sock
                server_addr = proxy_server or server_sock.getsockname()
            client_addr = proxy_client or sock.getpeername()
        except Exception:
            server_addr = ("127.0.0.1", 8000)
            client_addr = ("127.0.0.1", 0)

        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "http_version": "1.1",
            "method": req.method,
            "scheme": "https" if isinstance(sock, ssl.SSLSocket) else "http",
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
