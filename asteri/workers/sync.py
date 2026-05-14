import os
import socket
import sys
import io
import select
import time
from .base import BaseWorker
from ..http import build_http_response
from ..utils import logger, Colors

class SyncWorker(BaseWorker):
    def run(self):
        self.init_process()
        
        # Sockets are already non-blocking from Arbiter
        
        while self.alive:
            try:
                # Wait for any socket to be ready
                readable, _, _ = select.select(self.sockets, [], [], 1.0)
                
                for sock in readable:
                    client, addr = sock.accept()
                    self.handle_request(client)
                
                # Check parent process (Arbiter) is still alive
                if sys.platform != 'win32' and os.getppid() != self.ppid:
                    self.alive = False
                    break
                    
            except (socket.timeout, InterruptedError, BlockingIOError):
                continue
            except Exception as e:
                if self.alive:
                    logger.error(f"Accept error: {e}")
                    time.sleep(0.1) # Avoid busy loop on persistent error

    def handle_http(self, sock, req):
        """Standard WSGI handling for HTTP/1.1."""
        env = self.build_wsgi_environ(req)
        self.execute_wsgi(sock, env)

    def handle_uwsgi(self, sock, env):
        """WSGI handling for uWSGI protocol."""
        self.execute_wsgi(sock, env)

    def build_wsgi_environ(self, req):
        env = {
            'REQUEST_METHOD': req.method,
            'SCRIPT_NAME': '',
            'PATH_INFO': req.path.split('?')[0],
            'QUERY_STRING': req.path.split('?')[1] if '?' in req.path else '',
            'SERVER_NAME': 'localhost',
            'SERVER_PORT': '8000',
            'SERVER_PROTOCOL': 'HTTP/1.1',
            'wsgi.version': (1, 0),
            'wsgi.url_scheme': 'http',
            'wsgi.input': io.BytesIO(req.body or b""),
            'wsgi.errors': sys.stderr,
            'wsgi.multithread': False,
            'wsgi.multiprocess': True,
            'wsgi.run_once': False,
        }
        for k, v in req.headers.items():
            env[f"HTTP_{k.upper().replace('-', '_')}"] = v
        
        if 'content-type' in req.headers:
            env['CONTENT_TYPE'] = req.headers['content-type']
        if 'content-length' in req.headers:
            env['CONTENT_LENGTH'] = req.headers['content-length']
            
        return env

    def execute_wsgi(self, sock, env):
        response_data = []
        headers_set = []

        def start_response(status, headers, exc_info=None):
            headers_set.extend([status, headers])
            return response_data.append

        try:
            result = self.app(env, start_response)
            
            if not headers_set:
                status_code = 500
                headers = {"Content-Type": "text/plain"}
                body = b"Internal Server Error: Application failed to start response."
            else:
                status_code = int(headers_set[0].split()[0])
                headers = dict(headers_set[1])
                body = b"".join(result) if hasattr(result, '__iter__') else result
            if hasattr(result, 'close'):
                result.close()

            sock.sendall(build_http_response(status_code, headers, body))
            
            # Access Log
            status_color = Colors.GREEN if status_code < 400 else Colors.YELLOW if status_code < 500 else Colors.RED
            logger.info(f"{env['REQUEST_METHOD']} {env['PATH_INFO']} - {status_color}{status_code}{Colors.ENDC}")
            
        except Exception as e:
            logger.error(f"WSGI Error: {e}")
            sock.sendall(build_http_response(500, {}, f"WSGI Error: {e}"))
