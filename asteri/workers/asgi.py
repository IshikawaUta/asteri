import asyncio
import socket
import io
from .base import BaseWorker
from ..http import build_http_response

class ASGIWorker(BaseWorker):
    def run(self):
        self.init_process()
        asyncio.run(self.main_loop())

    async def main_loop(self):
        self.socket.setblocking(False)
        loop = asyncio.get_running_loop()
        
        while self.alive:
            try:
                client, addr = await loop.sock_accept(self.socket)
                asyncio.create_task(self.handle_asgi_request(client))
            except Exception:
                await asyncio.sleep(0.1)

    async def handle_asgi_request(self, sock):
        try:
            data = await asyncio.get_running_loop().sock_recv(sock, 4096)
            if not data: return

            from ..http import HTTPParser
            req = HTTPParser.parse(data)
            if not req: return

            scope = self.build_asgi_scope(req)
            
            response_started = False
            response_body = b""
            status_code = 200
            headers = []

            async def receive():
                return {'type': 'http.request', 'body': req.body or b"", 'more_body': False}

            async def send(message):
                nonlocal response_started, response_body, status_code, headers
                if message['type'] == 'http.response.start':
                    status_code = message['status']
                    headers = {k.decode('ascii'): v.decode('ascii') for k, v in message.get('headers', [])}
                    response_started = True
                elif message['type'] == 'http.response.body':
                    response_body += message.get('body', b"")
                    if not message.get('more_body', False):
                        await asyncio.get_running_loop().sock_sendall(
                            sock, 
                            build_http_response(status_code, headers, response_body)
                        )

            await self.app(scope, receive, send)
        except Exception:
            pass
        finally:
            sock.close()

    def build_asgi_scope(self, req):
        return {
            'type': 'http',
            'asgi': {'version': '3.0', 'spec_version': '2.0'},
            'http_version': '1.1',
            'method': req.method,
            'scheme': 'http',
            'path': req.path.split('?')[0],
            'query_string': req.path.split('?')[1].encode('ascii') if '?' in req.path else b'',
            'headers': [(k.encode('ascii'), v.encode('ascii')) for k, v in req.headers.items()],
            'client': ('127.0.0.1', 0),
            'server': ('127.0.0.1', 8000),
        }
