import asyncio
import socket
import os
from .base import BaseWorker
from ..http import build_http_response, HTTPParser
from ..utils import logger, access_logger, Colors


class ASGIWorker(BaseWorker):
    def run(self):
        # self.init_process() # Already called by Arbiter
        asyncio.run(self.main_loop())

    async def main_loop(self):
        tasks = []
        for sock in self.sockets:
            sock.setblocking(False)
            tasks.append(asyncio.create_task(self.accept_loop(sock)))

        await asyncio.gather(*tasks)

    async def accept_loop(self, sock):
        loop = asyncio.get_running_loop()
        h3_handler = None
        if sock.type == socket.SOCK_DGRAM:
            from asteri.http3 import HTTP3Handler

            h3_handler = HTTP3Handler(self)

        while self.alive:
            try:
                if sock.type == socket.SOCK_STREAM:
                    client, addr = await loop.sock_accept(sock)
                    asyncio.create_task(
                        self.handle_asgi_request(client, listener_sock=sock)
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
            metrics_text = self.generate_prometheus_metrics()
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

    async def handle_asgi_request(self, sock, listener_sock=None):
        try:
            self.metrics_active_connections += 1
            if self.stash:
                self.increment_shared_counter("metrics.active_connections", 1)
            data = await asyncio.get_running_loop().sock_recv(sock, 4096)
            if not data:
                return

            from asteri.utils import parse_proxy_protocol

            proxy_client, proxy_server, remaining = parse_proxy_protocol(data)
            data = remaining
            if not data:
                data = await asyncio.get_running_loop().sock_recv(sock, 4096)
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

            req = HTTPParser.parse(data)
            if not req:
                return

            scope = self.build_asgi_scope(
                req, sock, listener_sock, proxy_client, proxy_server
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

            # Handle body streaming
            content_length = int(req.headers.get("content-length", 0))
            body_already_read = len(req.body) if req.body else 0

            async def receive():
                nonlocal body_already_read
                if body_already_read < content_length:
                    # Need to read more body from socket
                    more_data = await asyncio.get_running_loop().sock_recv(sock, 8192)
                    body_already_read += len(more_data)
                    return {
                        "type": "http.request",
                        "body": more_data,
                        "more_body": body_already_read < content_length,
                    }
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
                            hint_lines.append(
                                f"{k.decode('ascii')}: {v.decode('ascii')}"
                            )
                        hint_lines.append("\r\n")
                        await asyncio.get_running_loop().sock_sendall(
                            sock, ("\r\n".join(hint_lines)).encode("utf-8")
                        )
                    except OSError:
                        pass
                elif message["type"] == "http.response.start":
                    status_code = message["status"]
                    headers = {
                        k.decode("ascii"): v.decode("ascii")
                        for k, v in message.get("headers", [])
                    }
                    response_started = True
                elif message["type"] == "http.response.body":
                    response_body += message.get("body", b"")
                    if not message.get("more_body", False):
                        await asyncio.get_running_loop().sock_sendall(
                            sock,
                            build_http_response(
                                status_code, headers, response_body),
                        )
                        # Record Prometheus Request metric
                        self.increment_request_metric(
                            req.method, "HTTP/1.1", status_code
                        )
                        # Access Log
                        status_color = (
                            Colors.GREEN
                            if status_code < 400
                            else Colors.YELLOW if status_code < 500 else Colors.RED
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
            self.metrics_active_connections -= 1
            if self.stash:
                self.increment_shared_counter("metrics.active_connections", -1)
            try:
                sock.close()
            except OSError:
                pass

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

    def build_asgi_scope(
        self, req, sock, listener_sock=None, proxy_client=None, proxy_server=None
    ):
        try:
            # Prefer listener_sock for server address, but fallback to client_sock's local address
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
            "scheme": "http",  # TODO: Detect https
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
