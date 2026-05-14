import os
import socket
import threading
import select
import time
from concurrent.futures import ThreadPoolExecutor
from .sync import SyncWorker

class GThreadWorker(SyncWorker):
    def __init__(self, age, ppid, sockets, app, timeout, threads=4):
        super().__init__(age, ppid, sockets, app, timeout)
        self.threads = threads

    def run(self):
        self.init_process()
        
        with ThreadPoolExecutor(max_workers=self.threads) as executor:
            while self.alive:
                try:
                    # Wait for any socket to be ready
                    readable, _, _ = select.select(self.sockets, [], [], 1.0)
                    
                    for sock in readable:
                        client, addr = sock.accept()
                        executor.submit(self.handle_request, client)
                    
                    # Check master
                    if os.getppid() != self.ppid:
                        self.alive = False
                        break
                except (socket.timeout, InterruptedError, BlockingIOError):
                    continue
                except Exception:
                    time.sleep(0.1)
