import os
import signal
import socket
import time
import psutil
from ..utils import logger, set_proctitle, Colors
from ..http import HTTPParser, HTTP2Handler, build_http_response
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
        self.dirty_apps = kwargs.get("dirty_apps", None)
        self.stash_address = kwargs.get("stash_address", None)
        if self.stash_address:
            from asteri.dirty import StashClient

            self.stash = StashClient(self.stash_address)
        else:
            self.stash = None

        # Prometheus & OpenTelemetry Metrics Local Store
        self.metrics_requests_total = {}
        self.metrics_active_connections = 0
        self.metrics_start_time = time.time()

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
        """Common logic to determine protocol and dispatch."""
        try:
            client_sock.settimeout(self.timeout)

            # Track active connection
            self.metrics_active_connections += 1
            if self.stash:
                self.increment_shared_counter("metrics.active_connections", 1)
            data = b""
            chunk = client_sock.recv(4096)
            if chunk:
                from asteri.utils import parse_proxy_protocol

                proxy_client, proxy_server, remaining = parse_proxy_protocol(
                    chunk)
                self._current_proxy_client = proxy_client
                self._current_proxy_server = proxy_server
                data = remaining
            else:
                self._current_proxy_client = None
                self._current_proxy_server = None

            while b"\r\n\r\n" not in data:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if len(data) > 32768:  # Safety limit for headers
                    break

            if not data:
                return

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
                return

            # Internal Prometheus metrics endpoint
            if b"GET /metrics" in data:
                metrics_text = self.generate_prometheus_metrics()
                client_sock.sendall(
                    build_http_response(
                        200,
                        {"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
                        metrics_text.encode("utf-8"),
                    )
                )
                logger.info(f"GET /metrics - {Colors.GREEN}200{Colors.ENDC}")
                return

            if HTTP2Handler.is_http2(data):
                h2_handler = HTTP2Handler(client_sock, initial_data=data)
                h2_handler.handle()
                return
            elif UWSGIHandler.is_uwsgi(data):
                # Handle large uWSGI packets (up to 64KB)
                import struct

                _, size, _ = struct.unpack("<BHB", data[:4])
                remaining = (size + 4) - len(data)
                while remaining > 0:
                    chunk = client_sock.recv(min(remaining, 8192))
                    if not chunk:
                        break
                    data += chunk
                    remaining -= len(chunk)

                vars, mod = UWSGIHandler.parse(data)
                if vars:
                    self.handle_uwsgi(client_sock, vars, listener_sock)
            else:
                req = HTTPParser.parse(data)
                if req:
                    self.handle_http(client_sock, req, listener_sock)
        except socket.timeout:
            # Idle connection, just close it silently
            pass
        except Exception as e:
            logger.error(f"Error handling request: {e}")
        finally:
            self.metrics_active_connections -= 1
            if self.stash:
                self.increment_shared_counter("metrics.active_connections", -1)
            try:
                client_sock.close()
            except Exception:
                pass

    def handle_http(self, sock, req):
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
            self.increment_shared_counter(stash_key, 1)

    def increment_shared_counter(self, key: str, value: int = 1):
        """Helper to increment/decrement a shared counter inside Stash server."""
        if self.stash:
            try:
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
