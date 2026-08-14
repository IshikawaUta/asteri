import os
from .sync import SyncWorker
from ..utils import logger

try:
    import gevent  # type: ignore[import-untyped]
    import gevent.monkey  # type: ignore[import-untyped]
    from gevent.server import StreamServer  # type: ignore[import-untyped]

    GEVENT_AVAILABLE = True
except ImportError:
    GEVENT_AVAILABLE = False


class GeventWorker(SyncWorker):
    def __init__(self, age, ppid, sockets, app, timeout, **kwargs):
        super().__init__(age, ppid, sockets, app, timeout, **kwargs)

    def init_process(self):
        if not GEVENT_AVAILABLE:
            raise RuntimeError(
                "Gevent is not installed. Please install it with 'pip install gevent' to use this worker."
            )
        # Monkey patch before importing the application so it uses
        # gevent-patched stdlib modules from the very start.
        gevent.monkey.patch_all()
        super().init_process()

    def run(self):
        if not GEVENT_AVAILABLE:
            logger.error(
                "Gevent is not installed. Please install it with 'pip install gevent' to use this worker."
            )
            return

        # Spawn a StreamServer for each socket
        servers = []
        for sock in self.sockets:

            def handle_factory(listener_sock):
                def handler(client_sock, address):
                    if not self.acquire_connection(client_sock):
                        return
                    try:
                        self.handle_request(
                            client_sock, listener_sock=listener_sock)
                    except Exception:
                        pass
                    finally:
                        self.release_connection()

                return handler

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
