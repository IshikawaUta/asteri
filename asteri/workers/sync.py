import os
import socket
import sys
import io
from .base import BaseWorker
from ..http import build_http_response

class SyncWorker(BaseWorker):
    def run(self):
        self.init_process()
        
        # Configure socket to be non-blocking with a timeout for select-like behavior
        self.socket.settimeout(1.0)
        
        while self.alive:
            try:
                client, addr = self.socket.accept()
                self.handle_request(client)
            except socket.timeout:
                # Check parent process (Arbiter) is still alive
                if sys.platform != 'win32' and os.getppid() != self.ppid:
                    self.alive = False
                    break
            except Exception as e:
                if self.alive:
                    pass # Log or handle accept errors

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
            status_code = int(headers_set[0].split()[0])
            headers = dict(headers_set[1])
            
            body = b"".join(result) if hasattr(result, '__iter__') else result
            if hasattr(result, 'close'):
                result.close()

            sock.sendall(build_http_response(status_code, headers, body))
        except Exception as e:
            sock.sendall(build_http_response(500, {}, f"WSGI Error: {e}"))
