import os
import signal
import socket
import time
from ..utils import logger, set_proctitle, Colors

class BaseWorker:
    def __init__(self, age, ppid, socket, app_path, timeout):
        self.age = age
        self.ppid = ppid
        self.socket = socket
        self.app_path = app_path
        self.app = None
        self.timeout = timeout
        self.alive = True
        self.booted = False

    def init_process(self):
        """Initialize worker process."""
        from ..utils import import_app
        self.app = import_app(self.app_path)
        
        # Set up signals
        signal.signal(signal.SIGQUIT, self.handle_quit)
        signal.signal(signal.SIGTERM, self.handle_exit)
        signal.signal(signal.SIGINT, self.handle_exit)
        
        # Reset signals to default that might have been ignored in parent
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        
        self.booted = True
        set_proctitle(f"worker [{self.__class__.__name__}]")
        logger.info(f"Worker spawned (pid: {Colors.BOLD}{os.getpid()}{Colors.ENDC})")

    def handle_quit(self, sig, frame):
        self.alive = False

    def handle_exit(self, sig, frame):
        self.alive = False
        os._exit(0)

    def run(self):
        raise NotImplementedError()

    def handle_request(self, client_sock):
        """Common logic to determine protocol and dispatch."""
        try:
            data = client_sock.recv(4096)
            if not data:
                return

            from ..http import HTTPParser, HTTP2Handler, build_http_response
            from ..uwsgi import UWSGIHandler

            # Internal Status Dashboard
            if b"GET /asteri-status" in data:
                status_body = f"Asteri Web Server Status\n"
                status_body += f"Worker PID: {os.getpid()}\n"
                status_body += f"Parent PID: {self.ppid}\n"
                status_body += f"Worker Type: {self.__class__.__name__}\n"
                client_sock.sendall(build_http_response(200, {"Content-Type": "text/plain"}, status_body))
                return

            if HTTP2Handler.is_http2(data):
                h2_handler = HTTP2Handler(client_sock)
                # We need to pass the initial data if it contains more than preface
                # But for now, handle() will recv more data
                h2_handler.handle()
                return
            elif UWSGIHandler.is_uwsgi(data):
                vars, mod = UWSGIHandler.parse(data)
                if vars:
                    self.handle_uwsgi(client_sock, vars)
            else:
                req = HTTPParser.parse(data)
                if req:
                    self.handle_http(client_sock, req)
        except Exception as e:
            logger.error(f"Error handling request: {e}")
        finally:
            try:
                client_sock.close()
            except:
                pass

    def handle_http(self, sock, req):
        raise NotImplementedError()

    def handle_uwsgi(self, sock, env):
        raise NotImplementedError()
