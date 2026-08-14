import os
import socket
import sys
import io
import ssl
import select
import time
import asyncio
import threading
from .base import BaseWorker
from ..http import build_http_response, chunked_encode_part, chunked_terminator, sanitize_header_name
from ..utils import logger, access_logger, Colors


class SyncWorker(BaseWorker):
    def run(self):
        # self.init_process() # Already called by Arbiter

        # Sockets are already non-blocking from Arbiter

        while self.alive:
            try:
                # Wait for any socket to be ready
                readable, _, _ = select.select(self.sockets, [], [], 1.0)

                for sock in readable:
                    if sock.type == socket.SOCK_STREAM:
                        client, addr = sock.accept()
                        self.guarded_handle_request(client, listener_sock=sock)
                    elif sock.type == socket.SOCK_DGRAM:
                        try:
                            data, addr = sock.recvfrom(65535)
                            if data:
                                if not hasattr(self, "_h3_loop"):
                                    from asteri.http3 import HTTP3Handler

                                    self._h3_handler = HTTP3Handler(self)
                                    self._h3_loop = asyncio.new_event_loop()
                                    threading.Thread(
                                        target=self._h3_loop.run_forever,
                                        daemon=True,
                                    ).start()
                                asyncio.run_coroutine_threadsafe(
                                    self._h3_handler.handle_packet(
                                        sock, data, addr),
                                    self._h3_loop,
                                )
                        except (BlockingIOError, OSError):
                            pass

                # Check parent process (Arbiter) is still alive
                if sys.platform != "win32" and os.getppid() != self.ppid:
                    self.alive = False
                    break

            except (socket.timeout, InterruptedError, BlockingIOError):
                continue
            except Exception as e:
                if self.alive:
                    logger.error(f"Accept error: {e}")
                    time.sleep(0.1)  # Avoid busy loop on persistent error

    def handle_http(self, sock, req, listener_sock=None, connector=None):
        """Standard WSGI handling for HTTP/1.1."""
        env = self.build_wsgi_environ(
            req, listener_sock, sock, connector=connector)
        self.execute_wsgi(sock, env)

    def handle_h2_request(self, method, path, headers, body, listener_sock=None, client_sock=None):
        """Dispatch an HTTP/2 request to the WSGI application.

        Returns (status_code, response_headers, response_body_bytes).
        """
        proxy_client = getattr(self, "_current_proxy_client", None)
        proxy_server = getattr(self, "_current_proxy_server", None)
        try:
            server_addr = proxy_server or (listener_sock or client_sock).getsockname()
        except Exception:
            server_addr = ("127.0.0.1", 8000)
        try:
            client_addr = proxy_client or client_sock.getpeername()
        except Exception:
            client_addr = ("127.0.0.1", 0)

        env = {
            "REQUEST_METHOD": method,
            "SCRIPT_NAME": "",
            "PATH_INFO": path.split("?")[0],
            "QUERY_STRING": path.split("?")[1] if "?" in path else "",
            "SERVER_NAME": server_addr[0],
            "SERVER_PORT": str(server_addr[1]),
            "SERVER_PROTOCOL": "HTTP/2",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": (
                "https" if isinstance(client_sock, ssl.SSLSocket) else "http"
            ),
            "wsgi.input": io.BytesIO(body or b""),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": True,
            "wsgi.run_once": False,
            "REMOTE_ADDR": client_addr[0],
            "REMOTE_PORT": str(client_addr[1]),
        }
        for k, v in headers.items():
            env[f"HTTP_{k.upper().replace('-', '_')}"] = v
        if "content-type" in headers:
            env["CONTENT_TYPE"] = headers["content-type"]
        if "content-length" in headers:
            env["CONTENT_LENGTH"] = headers["content-length"]

        result_iter = []
        headers_set = []

        def start_response(status, response_headers, exc_info=None):
            headers_set.extend([status, response_headers])
            return result_iter.append

        try:
            result = self.app(env, start_response)
            if hasattr(result, "__iter__"):
                body_out = b"".join(result)
            else:
                body_out = b"".join(result_iter) if result_iter else b""
            if hasattr(result, "close"):
                result.close()

            if not headers_set:
                status_code = 500
                out_headers = [("content-type", "text/plain")]
                out_body = b"Internal Server Error: Application failed to start response."
            else:
                status_code = int(headers_set[0].split()[0])
                out_headers = list(headers_set[1])
                out_body = body_out

            self.increment_request_metric(method, "HTTP/2", status_code)

            status_color = (
                Colors.GREEN
                if status_code < 400
                else Colors.YELLOW if status_code < 500 else Colors.RED
            )
            access_logger.info(
                f"{method} {path} - {status_color}{status_code}{Colors.ENDC}"
            )

            return status_code, out_headers, out_body
        except Exception as e:
            logger.error(f"WSGI (HTTP/2) Error: {e}")
            try:
                self.increment_request_metric(method, "HTTP/2", 500)
            except Exception:
                pass
            return 500, [("content-type", "text/plain")], f"WSGI Error: {e}".encode(
                "utf-8"
            )

    def handle_uwsgi(self, sock, env, listener_sock):
        """WSGI handling for uWSGI protocol."""
        # env is already populated from uWSGI packet, but we can augment it
        env.setdefault("SERVER_NAME", self._cached_server_addr(listener_sock)[0])
        env.setdefault("SERVER_PORT", str(self._cached_server_addr(listener_sock)[1]))
        self.execute_wsgi(sock, env)

    def _cached_server_addr(self, sock):
        cache = getattr(self, "_srv_addr_cache", None)
        if cache is None:
            cache = self._srv_addr_cache = {}
        fd = sock.fileno()
        addr = cache.get(fd)
        if addr is None:
            addr = sock.getsockname()
            cache[fd] = addr
        return addr

    def build_wsgi_environ(self, req, listener_sock, sock, connector=None):
        proxy_client = None
        proxy_server = None
        if connector:
            proxy_client = connector.get("proxy_client")
            proxy_server = connector.get("proxy_server")
        if proxy_client is None:
            proxy_client = getattr(self, "_current_proxy_client", None)
        if proxy_server is None:
            proxy_server = getattr(self, "_current_proxy_server", None)
        try:
            server_addr = proxy_server or self._cached_server_addr(listener_sock)
        except Exception:
            server_addr = ("127.0.0.1", 8000)

        try:
            client_addr = proxy_client or sock.getpeername()
        except Exception:
            client_addr = ("127.0.0.1", 0)

        # Body handling (support streaming for large bodies)
        content_length = int(req.headers.get("content-length", 0))
        initial_body = req.body or b""

        class WSGIInput:
            def __init__(self, sock, initial_data, total_length):
                self.sock = sock
                self.buffer = io.BytesIO(initial_data)
                self.total_length = total_length
                self.read_so_far = len(initial_data)

            def read(self, size=-1):
                if size == -1:
                    # Read everything
                    remaining = self.total_length - self.read_so_far
                    if remaining > 0:
                        data = self._read_from_sock(remaining)
                        self.buffer.seek(0, io.SEEK_END)
                        self.buffer.write(data)
                    return self.buffer.getvalue()

                # Check buffer first
                data = self.buffer.read(size)
                if not data and self.read_so_far < self.total_length:
                    # Read from socket
                    to_read = min(size, self.total_length - self.read_so_far)
                    data = self._read_from_sock(to_read)
                    return data
                return data

            def _read_from_sock(self, size):
                chunks = []
                while size > 0:
                    chunk = self.sock.recv(min(size, 8192))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size -= len(chunk)
                    self.read_so_far += len(chunk)
                return b"".join(chunks)

            def readline(self, size=-1):
                return self.buffer.readline(size)  # Simplified

        def send_early_hints(headers):
            try:
                hint_resp = ["HTTP/1.1 103 Early Hints"]
                for k, v in headers:
                    safe_k = sanitize_header_name(str(k))
                    safe_v = sanitize_header_name(str(v))
                    hint_resp.append(f"{safe_k}: {safe_v}")
                hint_resp.append("\r\n")
                sock.sendall(("\r\n".join(hint_resp)).encode("latin-1"))
            except OSError:
                pass

        env = {
            "REQUEST_METHOD": req.method,
            "SCRIPT_NAME": "",
            "PATH_INFO": req.path.split("?")[0],
            "QUERY_STRING": req.path.split("?")[1] if "?" in req.path else "",
            "SERVER_NAME": server_addr[0],
            "SERVER_PORT": str(server_addr[1]),
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": (
                "https" if isinstance(sock, ssl.SSLSocket) else "http"
            ),
            "wsgi.input": WSGIInput(sock, initial_body, content_length),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": True,
            "wsgi.run_once": False,
            "wsgi.early_hints": send_early_hints,
            "REMOTE_ADDR": client_addr[0],
            "REMOTE_PORT": str(client_addr[1]),
        }
        for k, v in req.headers.items():
            env[f"HTTP_{k.upper().replace('-', '_')}"] = v

        if "content-type" in req.headers:
            env["CONTENT_TYPE"] = req.headers["content-type"]
        if "content-length" in req.headers:
            env["CONTENT_LENGTH"] = req.headers["content-length"]

        return env

    def execute_wsgi(self, sock, env):
        response_data = []
        headers_set = []

        def start_response(status, headers, exc_info=None):
            headers_set.extend([status, headers])
            return response_data.append

        status_code = 500
        headers = {}
        try:
            result = self.app(env, start_response)

            if not headers_set:
                status_code = 500
                headers = {"Content-Type": "text/plain"}
                body = b"Internal Server Error: Application failed to start response."
                sock.sendall(build_http_response(status_code, headers, body))
            else:
                status_code = int(headers_set[0].split()[0])
                headers = dict(headers_set[1])

                if hasattr(result, "__iter__") and not isinstance(
                    result, (list, tuple)
                ):
                    self._stream_wsgi_body(
                        sock, status_code, headers, iter(result))
                else:
                    body = b"".join(result) if hasattr(
                        result, "__iter__") else result
                    sock.sendall(build_http_response(status_code, headers, body))
            if hasattr(result, "close"):
                result.close()

            # Increment Prometheus Request metric
            self.increment_request_metric(
                env["REQUEST_METHOD"],
                env.get("SERVER_PROTOCOL", "HTTP/1.1"),
                status_code,
            )

            # Access Log
            status_color = (
                Colors.GREEN
                if status_code < 400
                else Colors.YELLOW if status_code < 500 else Colors.RED
            )
            access_logger.info(
                f"{env['REQUEST_METHOD']} {env['PATH_INFO']} - {status_color}{status_code}{Colors.ENDC}"
            )

        except Exception as e:
            logger.error(f"WSGI Error: {e}")
            try:
                self.increment_request_metric(
                    env["REQUEST_METHOD"], env.get(
                        "SERVER_PROTOCOL", "HTTP/1.1"), 500
                )
            except Exception:
                pass
            sock.sendall(build_http_response(500, {}, f"WSGI Error: {e}"))

    def _stream_wsgi_body(self, sock, status_code, headers, iterator):
        """Send a chunked-transfer-encoded streaming response."""
        try:
            import http.client

            reason = http.client.responses.get(status_code, "Unknown")
            head = [f"HTTP/1.1 {status_code} {reason}", "Transfer-Encoding: chunked"]
            for key, value in headers.items():
                if key.lower() == "content-length":
                    continue
                head.append(
                    f"{sanitize_header_name(key)}: {sanitize_header_name(value)}"
                )
            sock.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))

            for data in iterator:
                if not data:
                    continue
                if isinstance(data, str):
                    data = data.encode("utf-8")
                elif isinstance(data, memoryview):
                    data = data.tobytes()
                sock.sendall(chunked_encode_part(data))
            sock.sendall(chunked_terminator())
        except (OSError, ValueError):
            pass
