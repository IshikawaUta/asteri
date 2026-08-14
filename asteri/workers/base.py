import os
import signal
import socket
import time
import psutil
from ..utils import logger, set_proctitle, Colors
from ..http import (
    HTTPParser,
    HTTP2Handler,
    HTTPError,
    build_error_response,
    build_http_response,
    header_dict,
    read_chunked_body,
    read_content_length_body,
)
from ..uwsgi import UWSGIHandler


class BaseWorker:
    def __init__(self, age, ppid, sockets, app_path, timeout, **kwargs):
        self.age = age
        self.ppid = ppid
        self.sockets = sockets
        self.app_path = app_path
        self.app = None
        self.timeout = timeout
        self.alive = True
        self.booted = False
        self.disable_dashboard = kwargs.get("disable_dashboard", False)
        self.disable_metrics = kwargs.get("disable_metrics", False)
        self.proxy_protocol = kwargs.get("proxy_protocol", False)
        self.dirty_apps = kwargs.get("dirty_apps", None)
        self.stash_address = kwargs.get("stash_address", None)
        if self.stash_address:
            from asteri.dirty import StashClient

            self.stash = StashClient(self.stash_address)
        else:
            self.stash = None

        # HTTP hardening / limits
        self.keep_alive = kwargs.get("keep_alive", 2) or 0
        self.worker_connections = kwargs.get("worker_connections", 0) or 0
        self.max_body_size = kwargs.get("max_body_size", 0) or 0
        self.limit_request_line = kwargs.get("limit_request_line", 4094)
        self.limit_request_fields = kwargs.get("limit_request_fields", 100)
        self.limit_request_field_size = kwargs.get("limit_request_field_size", 8190)
        self.http2_max_concurrent_streams = kwargs.get(
            "http2_max_concurrent_streams", 100
        )
        self.header_limit_total = 32768

        # Prometheus & OpenTelemetry Metrics Local Store
        self.metrics_requests_total = {}
        self.metrics_active_connections = 0
        self.metrics_start_time = time.time()
        self._metrics_pending = {}
        self._metrics_flush_interval = 5.0
        self._metrics_last_flush = time.time()
        self._metrics_cache = (0.0, "")
        self._metrics_lock = None

    @property
    def http_limits(self):
        return {
            "limit_request_line": self.limit_request_line,
            "limit_request_fields": self.limit_request_fields,
            "limit_request_field_size": self.limit_request_field_size,
        }

    def acquire_connection(self, client_sock):
        """Enforce worker_connections cap and register a new active connection."""
        if self.worker_connections and (
            self.metrics_active_connections >= self.worker_connections
        ):
            try:
                client_sock.close()
            except OSError:
                pass
            return False
        self.metrics_active_connections += 1
        if self.stash:
            self.increment_shared_counter("metrics.active_connections", 1)
        return True

    def release_connection(self):
        self.metrics_active_connections = max(
            0, self.metrics_active_connections - 1
        )
        if self.stash:
            self.increment_shared_counter("metrics.active_connections", -1)

    def guarded_handle_request(self, client_sock, listener_sock=None):
        """Acquire the connection slot, dispatch, then always release it."""
        self.submit_connection(
            self.handle_request, client_sock, listener_sock=listener_sock
        )

    def submit_connection(self, fn, client_sock, **kwargs):
        """Reserve an active-connection slot before running ``fn``.

        When worker_connections is reached, the socket is closed immediately
        so excess work never reaches a thread pool or accept loop.
        """
        if not self.acquire_connection(client_sock):
            return
        try:
            fn(client_sock, **kwargs)
        except Exception as e:
            logger.error(f"Error handling request: {e}")
            try:
                client_sock.sendall(
                    build_error_response(500, b"Internal Error"))
            except OSError:
                pass
        finally:
            self.release_connection()

    def init_process(self):
        """Initialize worker process."""
        os.environ["ASTERI_DISABLE_DASHBOARD"] = "1" if self.disable_dashboard else "0"
        from ..utils import import_app

        if self.dirty_apps:
            from asteri.dirty import DirtyAppLoader

            self.app = DirtyAppLoader(self.dirty_apps)
        else:
            self.app = import_app(self.app_path)

        # Set up signals
        signal.signal(signal.SIGQUIT, self.handle_quit)
        signal.signal(signal.SIGTERM, self.handle_exit)
        signal.signal(signal.SIGINT, self.handle_exit)

        # Reset signals to default that might have been ignored in parent
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)

        self.booted = True
        set_proctitle(f"worker [{self.__class__.__name__}]")
        logger.info(
            f"Worker spawned (pid: {Colors.BOLD}{os.getpid()}{Colors.ENDC})")

    def handle_quit(self, sig, frame):
        """Graceful shutdown."""
        self.alive = False

    def handle_exit(self, sig, frame):
        """Quick shutdown."""
        self.alive = False
        # For Sync/GThread, we might want to exit immediately
        # but for others, let the loop finish or use os._exit
        if self.__class__.__name__ in ["SyncWorker", "GThreadWorker"]:
            os._exit(0)

    def run(self):
        raise NotImplementedError()

    def handle_request(self, client_sock, listener_sock=None):
        """Common logic to determine protocol, enforce limits, and dispatch.

        HTTP/1.1 connections are kept alive when keep_alive is enabled; proxy
        protocol headers are only accepted when explicitly enabled.
        """
        try:
            client_sock.settimeout(self.timeout)
            try:
                client_sock.setsockopt(
                    socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

            buffer = bytearray()
            first_request = True

            while self.alive:
                # Read until the full header block is available
                while buffer.find(b"\r\n\r\n") == -1:
                    chunk = client_sock.recv(4096)
                    if not chunk:
                        return
                    buffer += chunk
                    if len(buffer) > self.header_limit_total:
                        client_sock.sendall(build_error_response(431))
                        return

                if first_request and self.proxy_protocol:
                    from asteri.utils import parse_proxy_protocol

                    proxy_client, proxy_server, remaining = parse_proxy_protocol(
                        bytes(buffer))
                    self._current_proxy_client = proxy_client
                    self._current_proxy_server = proxy_server
                    buffer = bytearray(remaining)
                    while buffer.find(b"\r\n\r\n") == -1:
                        chunk = client_sock.recv(4096)
                        if not chunk:
                            return
                        buffer += chunk
                        if len(buffer) > self.header_limit_total:
                            client_sock.sendall(build_error_response(431))
                            return
                elif first_request:
                    self._current_proxy_client = None
                    self._current_proxy_server = None

                first_request = False

                data = bytes(buffer)

                # Internal Status Dashboard
                if not self.disable_dashboard and b"GET /asteri-status" in data:
                    from ..utils import build_status_html

                    status_html = build_status_html(
                        self.__class__.__name__, os.getpid(), self.ppid
                    )
                    client_sock.sendall(
                        build_http_response(
                            200, {"Content-Type": "text/html"}, status_html)
                    )
                    logger.info(
                        f"GET /asteri-status - {Colors.GREEN}200{Colors.ENDC}")
                    if not self.keep_alive:
                        return
                    client_sock.settimeout(self.keep_alive)
                    buffer = bytearray(data.split(b"\r\n\r\n", 1)[1])
                    continue

                # Internal Prometheus metrics endpoint (cached ~1s)
                if b"GET /metrics" in data:
                    metrics_text = self._cached_metrics()
                    client_sock.sendall(
                        build_http_response(
                            200,
                            {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
                            metrics_text.encode("utf-8"),
                        )
                    )
                    logger.info(f"GET /metrics - {Colors.GREEN}200{Colors.ENDC}")
                    if not self.keep_alive:
                        return
                    client_sock.settimeout(self.keep_alive)
                    buffer = bytearray(data.split(b"\r\n\r\n", 1)[1])
                    continue

                if HTTP2Handler.is_http2(data):
                    h2_handler = HTTP2Handler(
                        client_sock,
                        initial_data=data,
                        request_handler=lambda m, p, h, b: self.handle_h2_request(
                            m, p, h, b, listener_sock, client_sock
                        ),
                        max_body_size=self.max_body_size,
                        max_concurrent_streams=self.http2_max_concurrent_streams,
                    )
                    h2_handler.handle()
                    return
                elif UWSGIHandler.is_uwsgi(data):
                    # Handle large uWSGI packets (up to 64KB)
                    import struct

                    _, size, _ = struct.unpack("<BHB", data[:4])
                    while len(buffer) < (size + 4):
                        chunk = client_sock.recv(8192)
                        if not chunk:
                            break
                        buffer += chunk
                    vars, mod = UWSGIHandler.parse(bytes(buffer))
                    if vars:
                        self.handle_uwsgi(client_sock, vars, listener_sock)
                    return
                else:
                    # HTTP/1.1
                    head, _, body_initial = data.partition(b"\r\n\r\n")
                    try:
                        req, extra = self._build_http_request(
                            client_sock, head, body_initial)
                    except HTTPError as e:
                        client_sock.sendall(build_error_response(e.status))
                        return

                    connector = {
                        "proxy_client": self._current_proxy_client,
                        "proxy_server": self._current_proxy_server,
                    }
                    self.handle_http(
                        client_sock, req, listener_sock, connector=connector
                    )

                    connection_hdr = req.headers.get("connection", "").lower()
                    close_conn = (
                        req.version != "HTTP/1.1"
                        or "close" in connection_hdr
                        or not self.keep_alive
                    )
                    if close_conn:
                        return
                    client_sock.settimeout(self.keep_alive)
                    buffer = bytearray(extra)
                    self._flush_metrics()
        except socket.timeout:
            # Idle connection, just close it silently
            pass
        except Exception as e:
            logger.error(f"Error handling request: {e}")
        finally:
            self._flush_metrics(force=True)
            try:
                client_sock.close()
            except Exception:
                pass

    def _build_http_request(self, client_sock, head, body_initial):
        """Parse headers, enforce limits, and read the (possibly chunked) body.

        Returns (request, leftover_bytes) where leftover_bytes may hold a
        pipelined next request for keep-alive connections.
        """
        headers = header_dict(head, self.http_limits)
        te = headers.get("transfer-encoding", "").lower()
        cl_header = headers.get("content-length")

        if te:
            if "chunked" not in te:
                raise HTTPError(501, "Unsupported Transfer-Encoding")
            body, extra = read_chunked_body(
                lambda: client_sock.recv(8192) or b"",
                body_initial,
                self.max_body_size,
            )
            headers["content-length"] = str(len(body))
            headers.pop("transfer-encoding", None)
        elif cl_header:
            try:
                total = int(cl_header.strip() or 0)
            except ValueError:
                raise HTTPError(400, "Invalid Content-Length")
            if total < 0:
                raise HTTPError(400, "Invalid Content-Length")
            body, extra = read_content_length_body(
                lambda: client_sock.recv(8192) or b"",
                body_initial,
                total,
                self.max_body_size,
            )
        else:
            body, extra = b"", body_initial

        head_bytes = head + b"\r\n\r\n"
        req = HTTPParser.parse_raw(head_bytes, body, headers)
        return req, extra

    def _flush_metrics(self, force=False):
        """Batch-push local request counters into the Stash server."""
        if not self._metrics_pending or not self.stash:
            self._metrics_pending = {}
            return
        now = time.time()
        if not force and now - self._metrics_last_flush < self._metrics_flush_interval:
            return
        self._metrics_last_flush = now
        pending, self._metrics_pending = self._metrics_pending, {}
        for key, delta in pending.items():
            try:
                self.increment_shared_counter(key, delta)
            except Exception:
                pass

    def _cached_metrics(self):
        """Return /metrics body, regenerated at most once per second."""
        now = time.time()
        if now - self._metrics_cache[0] < 1.0 and self._metrics_cache[1]:
            return self._metrics_cache[1]
        text = self.generate_prometheus_metrics()
        self._metrics_cache = (now, text)
        return text

    def handle_http(self, sock, req, listener_sock=None, connector=None):
        raise NotImplementedError()

    def handle_uwsgi(self, sock, env):
        raise NotImplementedError()

    def increment_request_metric(self, method: str, protocol: str, status_code: int):
        """Increment processed request counters in both local memory and Stash IPC store."""
        status_class = f"{status_code // 100}xx"
        key = (method, protocol, status_class)
        self.metrics_requests_total[key] = self.metrics_requests_total.get(
            key, 0) + 1

        if self.stash:
            stash_key = f"metrics.requests_total.{method}.{protocol}.{status_class}"
            self._metrics_pending[stash_key] = (
                self._metrics_pending.get(stash_key, 0) + 1
            )

    def increment_shared_counter(self, key: str, value: int = 1):
        """Helper to increment/decrement a shared counter inside Stash server."""
        if not self.stash:
            return
        try:
            if hasattr(self.stash, "increment") and callable(self.stash.increment):
                self.stash.increment(key, value)
                return
            current_bytes = self.stash.get(key)
            current = int(current_bytes.decode(
                "utf-8")) if current_bytes else 0
            self.stash.set(key, str(current + value).encode("utf-8"))
        except Exception:
            pass

    def generate_prometheus_metrics(self) -> str:
        """Generate standard Prometheus & OpenTelemetry text exposition format."""
        import time

        cpu_usage = psutil.cpu_percent()
        mem = psutil.virtual_memory()
        uptime = int(time.time() - self.metrics_start_time)

        active_conn = self.metrics_active_connections
        reqs = {}

        if self.stash:
            # Active connections aggregated from Stash
            active_bytes = self.stash.get("metrics.active_connections")
            if active_bytes:
                try:
                    active_conn = int(active_bytes.decode("utf-8"))
                except Exception:
                    pass

            # Aggregate all seen request metrics keys from Stash
            for key, val in self.metrics_requests_total.items():
                method, protocol, status_class = key
                stash_key = f"metrics.requests_total.{method}.{protocol}.{status_class}"
                stash_bytes = self.stash.get(stash_key)
                if stash_bytes:
                    try:
                        reqs[key] = int(stash_bytes.decode("utf-8"))
                    except Exception:
                        reqs[key] = val
                else:
                    reqs[key] = val
        else:
            reqs = self.metrics_requests_total

        workers_count = 1
        if self.stash:
            wc_bytes = self.stash.get("metrics.workers_count")
            if wc_bytes:
                try:
                    workers_count = int(wc_bytes.decode("utf-8"))
                except Exception:
                    pass

        lines = [
            "# HELP asteri_workers_count Number of active workers",
            "# TYPE asteri_workers_count gauge",
            f"asteri_workers_count {workers_count}",
            "",
            "# HELP asteri_requests_total Total number of HTTP requests processed",
            "# TYPE asteri_requests_total counter",
        ]

        # Standard Prometheus metrics format
        for (method, protocol, status_class), count in reqs.items():
            lines.append(
                f'asteri_requests_total{{method="{method}",protocol="{protocol}",status_class="{status_class}"}} {count}'
            )

        lines.extend(
            [
                "",
                "# HELP asteri_active_connections Number of active connections",
                "# TYPE asteri_active_connections gauge",
                f"asteri_active_connections {active_conn}",
                "",
                "# HELP asteri_cpu_usage_percent CPU usage percentage",
                "# TYPE asteri_cpu_usage_percent gauge",
                f"asteri_cpu_usage_percent {cpu_usage}",
                "",
                "# HELP asteri_memory_usage_percent Memory usage percentage",
                "# TYPE asteri_memory_usage_percent gauge",
                f"asteri_memory_usage_percent {mem.percent}",
                "",
                "# HELP asteri_uptime_seconds System uptime in seconds",
                "# TYPE asteri_uptime_seconds counter",
                f"asteri_uptime_seconds {uptime}",
                "",
                "# OpenTelemetry Semantic Conventions Metrics Integration",
                "# HELP http_server_active_requests Number of active HTTP requests",
                "# TYPE http_server_active_requests gauge",
                f"http_server_active_requests {active_conn}",
                "",
                "# HELP http_server_duration_milliseconds_count Total number of completed requests",
                "# TYPE http_server_duration_milliseconds_count counter",
            ]
        )

        # OpenTelemetry semantic conventions format
        for (method, protocol, status_class), count in reqs.items():
            flavor = "1.1"
            if "2" in protocol:
                flavor = "2.0"
            elif "3" in protocol:
                flavor = "3.0"

            lines.append(
                f'http_server_duration_milliseconds_count{{http_method="{method}",http_status_code="{status_class}",http_flavor="{flavor}"}} {count}'
            )

        return "\n".join(lines) + "\n"
