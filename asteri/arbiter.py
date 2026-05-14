import os
import signal
import socket
import sys
import time
from .utils import logger, set_proctitle, Colors

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

class Arbiter:
    """The Master process that manages workers."""
    
    def __init__(self, app_path, worker_class, num_workers=1, binds=None, reload=False, certfile=None, keyfile=None,
                 daemon=False, pidfile=None, user=None, group=None, umask=0, proc_name=None, timeout=30):
        self.app_path = app_path
        self.worker_class = worker_class
        self.num_workers = num_workers
        self.binds = binds or ["127.0.0.1:8000"]
        self.reload = reload
        self.certfile = certfile
        self.keyfile = keyfile
        self.daemon = daemon
        self.pidfile = pidfile
        self.user = user
        self.group = group
        self.umask = umask
        self.proc_name = proc_name or "master"
        self.timeout = timeout
        self.workers = {} # pid -> worker_instance
        self.socks = [] # list of listening sockets
        self.alive = True
        self.pid = os.getpid()
        self.reloader = None

    def start(self):
        if self.daemon:
            self.daemonize()
        
        if self.pidfile:
            self.write_pid()
            
        if self.umask:
            os.umask(self.umask)

        set_proctitle(self.proc_name)
        logger.info(f"Starting Asteri Arbiter (pid: {Colors.BOLD}{os.getpid()}{Colors.ENDC})")
        
        if self.reload and WATCHDOG_AVAILABLE:
            self.setup_reloader()
        elif self.reload:
            logger.warning("Watchdog not available, falling back to manual reload.")
        
        # Create listener sockets for all binds
        for bind in self.binds:
            try:
                host, port = bind.split(":")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                
                if self.certfile and self.keyfile:
                    import ssl
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
                    sock = context.wrap_socket(sock, server_side=True)
                
                sock.bind((host, int(port)))
                sock.listen(1024)
                sock.setblocking(False)
                self.socks.append(sock)
                
                scheme = "https" if self.certfile else "http"
                logger.info(f"Listening on {Colors.UNDERLINE}{scheme}://{bind}{Colors.ENDC}")
            except Exception as e:
                logger.error(f"Failed to bind to {bind}: {e}")
                self.stop()
                sys.exit(1)
        
        self.setup_signals()
        self.manage_workers()

    def setup_reloader(self):
        class ReloadHandler(FileSystemEventHandler):
            def __init__(self, arbiter):
                self.arbiter = arbiter
            def on_modified(self, event):
                if event.src_path.endswith('.py'):
                    logger.info(f"Change detected in {Colors.BOLD}{os.path.basename(event.src_path)}{Colors.ENDC}. Reloading...")
                    self.arbiter.stop_workers(signal.SIGTERM)

        self.reloader = Observer()
        self.reloader.schedule(ReloadHandler(self), os.getcwd(), recursive=True)
        self.reloader.start()
        logger.info(f"Auto-reload {Colors.GREEN}enabled{Colors.ENDC} (watchdog)")

    def daemonize(self):
        """Standard double-fork daemonization."""
        if os.fork() > 0: sys.exit(0)
        os.setsid()
        if os.fork() > 0: sys.exit(0)
        
        # Redirect standard file descriptors
        sys.stdout.flush()
        sys.stderr.flush()
        si = open(os.devnull, 'r')
        so = open(os.devnull, 'a+')
        se = open(os.devnull, 'a+')
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())

    def write_pid(self):
        with open(self.pidfile, 'w') as f:
            f.write(str(os.getpid()))

    def switch_user(self):
        if not self.user and not self.group:
            return
        import pwd, grp
        if self.group:
            gid = grp.getgrnam(self.group).gr_gid
            os.setgid(gid)
        if self.user:
            uid = pwd.getpwnam(self.user).pw_uid
            os.setuid(uid)

    def setup_signals(self):
        signal.signal(signal.SIGCHLD, self.handle_chld)
        signal.signal(signal.SIGTERM, self.handle_exit)
        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGHUP, self.handle_hup)
        signal.signal(signal.SIGQUIT, self.handle_quit)

    def handle_chld(self, sig, frame):
        """Worker died."""
        self.wakeup()

    def handle_exit(self, sig, frame):
        self.alive = False
        self.stop_workers(signal.SIGTERM)

    def handle_quit(self, sig, frame):
        self.alive = False
        self.stop_workers(signal.SIGQUIT)

    def handle_hup(self, sig, frame):
        """Reload workers."""
        logger.info("Reloading workers...")
        self.stop_workers(signal.SIGTERM)

    def wakeup(self):
        """Force loop to check workers."""
        pass

    def stop_workers(self, sig):
        import psutil
        for pid in list(self.workers.keys()):
            try:
                p = psutil.Process(pid)
                p.send_signal(sig)
            except psutil.NoSuchProcess:
                if pid in self.workers:
                    del self.workers[pid]

    def spawn_worker(self):
        worker = self.worker_class(0, self.pid, self.socks, self.app_path, self.timeout)
        pid = os.fork()
        
        if pid == 0: # Child
            try:
                self.switch_user()
                worker.init_process()
                worker.run()
                sys.exit(0)
            except Exception as e:
                logger.error(f"Worker error: {e}")
                sys.exit(1)
        else: # Parent
            self.workers[pid] = worker
            return pid

    def manage_workers(self):
        while self.alive:
            # Maintain worker count
            while len(self.workers) < self.num_workers:
                self.spawn_worker()
            
            # Reaping dead children
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
                while pid > 0:
                    if pid in self.workers:
                        del self.workers[pid]
                    pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                pass
                
            time.sleep(1.0)
            
        # Cleanup
        if self.reloader:
            self.reloader.stop()
            self.reloader.join()
        
        logger.info("Asteri shutting down.")
        for sock in self.socks:
            try:
                sock.close()
            except:
                pass
