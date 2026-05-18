import os
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
    def __init__(self, age, ppid, sockets, app, timeout, **kwargs):
        super().__init__(age, ppid, sockets, app, timeout, **kwargs)

    def run(self):
        if not GEVENT_AVAILABLE:
            logger.error(
                "Gevent is not installed. Please install it with 'pip install gevent' to use this worker."
            )
            return

        # Monkey patch
        gevent.monkey.patch_all()

        self.init_process()

        # Spawn a StreamServer for each socket
        servers = []
        for sock in self.sockets:

            def handle_factory(listener_sock):
                return lambda client_sock, address: self.handle_request(
                    client_sock, listener_sock=listener_sock
                )

            server = StreamServer(sock, handle_factory(sock))
            servers.append(gevent.spawn(server.serve_forever))

        # Wait for master to die
        while self.alive:
            if os.getppid() != self.ppid:
                self.alive = False
                break
            gevent.sleep(1.0)

        for s in servers:
            s.kill()
