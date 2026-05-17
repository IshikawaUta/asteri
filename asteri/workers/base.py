import os
import signal
import socket
import time
import psutil
import platform
from datetime import datetime
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
        logger.info(f"Worker spawned (pid: {Colors.BOLD}{os.getpid()}{Colors.ENDC})")

    def handle_quit(self, sig, frame):
        """Graceful shutdown."""
        self.alive = False

    def handle_exit(self, sig, frame):
        """Quick shutdown."""
        self.alive = False
        # For Sync/GThread, we might want to exit immediately
        # but for others, let the loop finish or use os._exit
        if self.__class__.__name__ in ['SyncWorker', 'GThreadWorker']:
            os._exit(0)

    def run(self):
        raise NotImplementedError()

    def handle_request(self, client_sock, listener_sock=None):
        """Common logic to determine protocol and dispatch."""
        try:
            client_sock.settimeout(self.timeout)
            data = b""
            chunk = client_sock.recv(4096)
            if chunk:
                from asteri.utils import parse_proxy_protocol
                proxy_client, proxy_server, remaining = parse_proxy_protocol(chunk)
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
                if len(data) > 32768: # Safety limit for headers
                    break

            if not data:
                return

            # Internal Status Dashboard
            if not self.disable_dashboard and b"GET /asteri-status" in data:
                from ..utils import build_status_html
                status_html = build_status_html(self.__class__.__name__, os.getpid(), self.ppid)
                client_sock.sendall(build_http_response(200, {"Content-Type": "text/html"}, status_html))
                logger.info(f"GET /asteri-status - {Colors.GREEN}200{Colors.ENDC}")
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
                    if not chunk: break
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
            try:
                client_sock.close()
            except:
                pass

    def handle_http(self, sock, req):
        raise NotImplementedError()

    def handle_uwsgi(self, sock, env):
        raise NotImplementedError()
