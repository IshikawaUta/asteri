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
    def run(self):
        if not GEVENT_AVAILABLE:
            logger.error("Gevent is not installed. Please install it with 'pip install gevent' to use this worker.")
            return

        # Monkey patch
        gevent.monkey.patch_all()
        
        self.init_process()
        
        def handle(client_sock, address):
            self.handle_request(client_sock)

        server = StreamServer(self.socket, handle)
        server.serve_forever()
