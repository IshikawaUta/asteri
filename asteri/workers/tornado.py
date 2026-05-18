import os
import sys
from .base import BaseWorker
from ..utils import logger

try:
    import tornado.web
    import tornado.httpserver
    import tornado.ioloop
    import tornado.wsgi

    TORNADO_AVAILABLE = True
except ImportError:
    TORNADO_AVAILABLE = False


class TornadoDashboardMiddleware:
    def __init__(self, wsgi_app, disable_dashboard, worker):
        self.wsgi_app = wsgi_app
        self.disable_dashboard = disable_dashboard
        self.worker = worker

    def __call__(self, environ, start_response):
        path_info = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET")

        # Intercept and serve /asteri-status dashboard dynamically
        if (
            path_info == "/asteri-status"
            and method == "GET"
            and not self.disable_dashboard
        ):
            from ..utils import build_status_html, Colors

            worker_type = "TornadoWorker"
            if len(sys.argv) > 0:
                for arg in sys.argv:
                    if "gtornado" in arg:
                        worker_type = "TornadoWorker (GTornado)"
                        break

            status_html = build_status_html(
                worker_type, os.getpid(), self.worker.ppid)

            logger.info(
                f"{method} {path_info} - {Colors.GREEN}200{Colors.ENDC}")

            status = "200 OK"
            headers = [("Content-Type", "text/html; charset=utf-8")]
            start_response(status, headers)
            return [status_html.encode("utf-8")]

        # Intercept and serve /metrics dynamically
        if path_info == "/metrics" and method == "GET":
            from ..utils import Colors

            metrics_text = self.worker.generate_prometheus_metrics()
            status = "200 OK"
            headers = [
                ("Content-Type", "text/plain; version=0.0.4; charset=utf-8")]
            start_response(status, headers)
            logger.info(f"GET /metrics - {Colors.GREEN}200{Colors.ENDC}")
            return [metrics_text.encode("utf-8")]

        # Track active connection
        self.worker.metrics_active_connections += 1
        if self.worker.stash:
            self.worker.increment_shared_counter(
                "metrics.active_connections", 1)

        recorded_status = ["200 OK"]

        def wrapped_start_response(status, headers, exc_info=None):
            recorded_status[0] = status
            return start_response(status, headers, exc_info)

        try:
            res = self.wsgi_app(environ, wrapped_start_response)

            # Request completed successfully!
            try:
                status_code = int(recorded_status[0].split()[0])
                self.worker.increment_request_metric(
                    method, "HTTP/1.1", status_code)

                # Standard request logging for Tornado
                from ..utils import Colors

                status_color = (
                    Colors.GREEN
                    if status_code < 400
                    else Colors.YELLOW if status_code < 500 else Colors.RED
                )
                logger.info(
                    f"{method} {path_info} - {status_color}{status_code}{Colors.ENDC}"
                )
            except Exception:
                pass

            return res
        except Exception as e:
            try:
                self.worker.increment_request_metric(method, "HTTP/1.1", 500)
            except Exception:
                pass
            raise e
        finally:
            self.worker.metrics_active_connections -= 1
            if self.worker.stash:
                self.worker.increment_shared_counter(
                    "metrics.active_connections", -1)


class TornadoWorker(BaseWorker):
    """Worker class utilizing Tornado's IOLoop and HTTPServer."""

    def __init__(self, age, ppid, sockets, app_path, timeout, **kwargs):
        super().__init__(age, ppid, sockets, app_path, timeout, **kwargs)

    def run(self):
        if not TORNADO_AVAILABLE:
            logger.error(
                "Tornado is not installed. Please install it to use this worker."
            )
            sys.exit(1)

        self.init_process()

        # Wrap our WSGI application in our native dashboard middleware first
        wrapped_app = TornadoDashboardMiddleware(
            self.app, self.disable_dashboard, self)

        # Wrap our WSGI application in Tornado's WSGIContainer
        container = tornado.wsgi.WSGIContainer(wrapped_app)

        # Set up HTTPServer with the WSGI container as its request callback
        server = tornado.httpserver.HTTPServer(container)

        # Add worker sockets
        for sock in self.sockets:
            # Sockets passed to add_socket must be listening and set to non-blocking
            sock.setblocking(False)
            server.add_socket(sock)

        loop = tornado.ioloop.IOLoop.current()

        # Periodic watchdog to check if the Arbiter has exited
        def check_parent():
            if os.getppid() != self.ppid or not self.alive:
                logger.info(f"Tornado worker exiting (pid: {os.getpid()})")
                server.stop()
                loop.stop()

        monitor = tornado.ioloop.PeriodicCallback(check_parent, 1000)
        monitor.start()

        try:
            loop.start()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            server.stop()
