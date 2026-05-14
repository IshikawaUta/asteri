import asyncio
import socket
import io
import os
import psutil
import platform
import traceback
from datetime import datetime
from .base import BaseWorker
from ..http import build_http_response, HTTPParser
from ..utils import logger, Colors

class ASGIWorker(BaseWorker):
    def run(self):
        self.init_process()
        asyncio.run(self.main_loop())

    async def main_loop(self):
        tasks = []
        for sock in self.sockets:
            sock.setblocking(False)
            tasks.append(asyncio.create_task(self.accept_loop(sock)))
        
        await asyncio.gather(*tasks)

    async def accept_loop(self, sock):
        loop = asyncio.get_running_loop()
        while self.alive:
            try:
                client, addr = await loop.sock_accept(sock)
                asyncio.create_task(self.handle_asgi_request(client))
            except Exception:
                await asyncio.sleep(0.1)

    async def handle_asgi_status(self, sock):
        try:
            mem = psutil.virtual_memory()
            cpu_usage = psutil.cpu_percent()
            boot_time = datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")

            status_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Asteri Status</title>
    <style>
        :root {{
            --bg: #0f172a;
            --card: rgba(30, 41, 59, 0.7);
            --primary: #6366f1;
            --accent: #a855f7;
            --text: #f8fafc;
            --text-dim: #94a3b8;
            --success: #22c55e;
        }}
        body {{
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .container {{ width: 90%; max-width: 800px; background: var(--card); backdrop-filter: blur(12px); border: 1px solid rgba(255,255,255,0.1); border-radius: 24px; padding: 2rem; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            padding-bottom: 1rem;
        }}
        h1 {{ margin: 0; font-size: 1.8rem; background: linear-gradient(to right, #818cf8, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .badge {{ background: var(--success); color: white; padding: 0.2rem 0.8rem; border-radius: 99px; font-size: 0.8rem; font-weight: bold; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem; }}
        .stat-card {{ background: rgba(255,255,255,0.05); padding: 1.5rem; border-radius: 16px; border: 1px solid rgba(255,255,255,0.05); transition: transform 0.3s; }}
        .stat-card:hover {{ transform: translateY(-5px); background: rgba(255,255,255,0.08); }}
        .stat-label {{ color: var(--text-dim); font-size: 0.9rem; margin-bottom: 0.5rem; text-transform: uppercase; letter-spacing: 1px; }}
        .stat-value {{ font-size: 1.5rem; font-weight: bold; color: var(--text); }}
        .footer {{ margin-top: 2rem; text-align: center; color: var(--text-dim); font-size: 0.8rem; opacity: 0.6; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌟 Asteri Dashboard</h1>
            <span class="badge">RUNNING</span>
        </div>
        <div class="grid">
            <div class="stat-card">
                <div class="stat-label">Worker PID</div>
                <div class="stat-value">{os.getpid()}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Parent PID</div>
                <div class="stat-value">{self.ppid}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Worker Type</div>
                <div class="stat-value">{self.__class__.__name__}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">CPU Usage</div>
                <div class="stat-value">{cpu_usage}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Memory Usage</div>
                <div class="stat-value">{mem.percent}%</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Operating System</div>
                <div class="stat-value">{platform.system()}</div>
            </div>
        </div>
        <div class="footer">
            Asteri Web Server v1.1.1 &bull; {datetime.now().strftime("%H:%M:%S")}
        </div>
    </div>
</body>
</html>"""
            await asyncio.get_running_loop().sock_sendall(
                sock, 
                build_http_response(200, {"Content-Type": "text/html"}, status_html)
            )
            logger.info(f"GET /asteri-status - {Colors.GREEN}200{Colors.ENDC}")
        except Exception:
            pass

    async def handle_asgi_request(self, sock):
        try:
            data = await asyncio.get_running_loop().sock_recv(sock, 4096)
            if not data: return

            # Internal Status Dashboard
            if b"GET /asteri-status" in data:
                await self.handle_asgi_status(sock)
                return

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
                        # Access Log
                        status_color = Colors.GREEN if status_code < 400 else Colors.YELLOW if status_code < 500 else Colors.RED
                        logger.info(f"{req.method} {req.path} - {status_color}{status_code}{Colors.ENDC}")

            await self.app(scope, receive, send)
        except Exception as e:
            logger.error(f"ASGI Error: {e}")
            logger.error(traceback.format_exc())
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
