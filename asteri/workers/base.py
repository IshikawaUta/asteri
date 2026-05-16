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
            if b"GET /asteri-status" in data:
                cpu_usage = psutil.cpu_percent()
                mem = psutil.virtual_memory()
                
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
            background-image: radial-gradient(circle at top right, #1e1b4b, transparent),
                               radial-gradient(circle at bottom left, #1e1b4b, transparent);
        }}
        .container {{
            width: 90%;
            max-width: 800px;
            background: var(--card);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 24px;
            padding: 2rem;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
        }}
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
            Asteri Web Server v1.2.1 &bull; {datetime.now().strftime("%H:%M:%S")}
        </div>
    </div>
</body>
</html>"""
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
