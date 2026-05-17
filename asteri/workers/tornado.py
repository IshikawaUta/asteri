import os
import sys
import signal
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
        path_info = environ.get('PATH_INFO', '/')
        method = environ.get('REQUEST_METHOD', 'GET')
        
        # Intercept and serve /asteri-status dashboard dynamically
        if path_info == '/asteri-status' and method == 'GET' and not self.disable_dashboard:
            from ..utils import build_status_html, Colors
            
            worker_type = "TornadoWorker"
            if len(sys.argv) > 0:
                for arg in sys.argv:
                    if "gtornado" in arg:
                        worker_type = "TornadoWorker (GTornado)"
                        break
                        
            status_html = build_status_html(
                worker_type,
                os.getpid(),
                self.worker.ppid
            )
            
            logger.info(f"{method} {path_info} - {Colors.GREEN}200{Colors.ENDC}")
            
            status = '200 OK'
            headers = [('Content-Type', 'text/html; charset=utf-8')]
            start_response(status, headers)
            return [status_html.encode('utf-8')]

        # Standard request logging for Tornado
        from ..utils import Colors
        logger.info(f"{method} {path_info} - {Colors.GREEN}200{Colors.ENDC}")
        return self.wsgi_app(environ, start_response)


class TornadoWorker(BaseWorker):
    """Worker class utilizing Tornado's IOLoop and HTTPServer."""
    
    def __init__(self, age, ppid, sockets, app_path, timeout, **kwargs):
        super().__init__(age, ppid, sockets, app_path, timeout, **kwargs)

    def run(self):
        if not TORNADO_AVAILABLE:
            logger.error("Tornado is not installed. Please install it to use this worker.")
            sys.exit(1)

        self.init_process()

        # Wrap our WSGI application in our native dashboard middleware first
        wrapped_app = TornadoDashboardMiddleware(self.app, self.disable_dashboard, self)

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
