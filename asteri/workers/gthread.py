import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from .sync import SyncWorker

class GThreadWorker(SyncWorker):
    def __init__(self, age, ppid, socket, app, timeout, threads=4):
        super().__init__(age, ppid, socket, app, timeout)
        self.threads = threads

    def run(self):
        self.init_process()
        self.socket.settimeout(1.0)
        
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            while self.alive:
                try:
                    client, addr = self.socket.accept()
                    executor.submit(self.handle_request, client)
                except socket.timeout:
                    if os.getppid() != self.ppid:
                        self.alive = False
                except Exception:
                    continue
