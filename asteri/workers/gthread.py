import os
import socket
import select
import time
from concurrent.futures import ThreadPoolExecutor
from .sync import SyncWorker


class GThreadWorker(SyncWorker):
    def __init__(self, age, ppid, sockets, app, timeout, threads=4, **kwargs):
        super().__init__(age, ppid, sockets, app, timeout, **kwargs)
        self.threads = threads

    def run(self):
        # self.init_process() # Already called by Arbiter

        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            while self.alive:
                try:
                    # Wait for any socket to be ready
                    readable, _, _ = select.select(self.sockets, [], [], 1.0)

                    for sock in readable:
                        client, addr = sock.accept()
                        executor.submit(
                            self._run_guarded, client, sock)

                    # Check master
                    if os.getppid() != self.ppid:
                        self.alive = False
                        break
                except (socket.timeout, InterruptedError, BlockingIOError):
                    continue
                except Exception:
                    time.sleep(0.1)

    def _run_guarded(self, client, listener_sock):
        if not self.acquire_connection(client):
            return
        try:
            self.handle_request(client, listener_sock=listener_sock)
        except Exception:
            pass
        finally:
            self.release_connection()
