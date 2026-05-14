import os
import socket
from .sync import SyncWorker
from ..utils import logger

try:
    import gevent
    import gevent.monkey
    from gevent.server import StreamServer
    GEVENT_AVAILABLE = True
except ImportError:
    GEVENT_AVAILABLE = False

class GeventWorker(SyncWorker):
    def __init__(self, age, ppid, sockets, app, timeout):
        super().__init__(age, ppid, sockets, app, timeout)

    def run(self):
        if not GEVENT_AVAILABLE:
            logger.error("Gevent is not installed. Please install it with 'pip install gevent' to use this worker.")
            return

        # Monkey patch
        gevent.monkey.patch_all()
        
        self.init_process()
        
        def handle(client_sock, address):
            self.handle_request(client_sock)

        # Spawn a StreamServer for each socket
        servers = []
        for sock in self.sockets:
            server = StreamServer(sock, handle)
            servers.append(gevent.spawn(server.serve_forever))
            
        # Wait for master to die
        while self.alive:
            if os.getppid() != self.ppid:
                self.alive = False
                break
            gevent.sleep(1.0)
            
        for s in servers:
            s.kill()
