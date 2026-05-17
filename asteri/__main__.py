import logging
import argparse
import sys
import os
import importlib
from asteri.arbiter import Arbiter
from asteri.workers.sync import SyncWorker
from asteri.workers.gthread import GThreadWorker
from asteri.workers.asgi import ASGIWorker
try:
    from asteri.workers.gevent import GeventWorker
except ImportError:
    GeventWorker = None
try:
    from asteri.workers.tornado import TornadoWorker
except ImportError:
    TornadoWorker = None
from asteri.utils import logger, print_banner, Colors, setup_logging, setup_access_logging

def import_app(app_path):
    """Import application from string 'module:callable'."""
    try:
        module_path, app_name = app_path.split(":")
        sys.path.insert(0, os.getcwd())
        module = importlib.import_module(module_path)
        return getattr(module, app_name)
    except Exception as e:
        logger.error(f"Could not import app '{Colors.BOLD}{app_path}{Colors.ENDC}': {e}")
        sys.exit(1)

def main():
    print_banner()
    
    parser = argparse.ArgumentParser(
        prog="asteri",
        description=f"{Colors.BOLD}Asteri: High Performance Web Server{Colors.ENDC}",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        add_help=True
    )
    
    # Positional Arguments
    parser.add_argument("app", nargs="?", help="Application path (e.g., myapp:app)")
    
    # Config Group
    config_group = parser.add_argument_group("Config")
    config_group.add_argument("-c", "--config", help="The Asteri config file.")
    config_group.add_argument("-v", "--version", action="version", version="Asteri v1.2.2")
    config_group.add_argument("--check-config", action="store_true", help="Check the configuration and exit.")
    config_group.add_argument("--print-config", action="store_true", help="Print the configuration settings.")

    # Network Group
    net_group = parser.add_argument_group("Network")
    net_group.add_argument("-b", "--bind", action="append", help="The socket to bind.")
    net_group.add_argument("--backlog", type=int, default=2048, help="The maximum number of pending connections.")
    net_group.add_argument("--reuse-port", action="store_true", help="Set the SO_REUSEPORT flag.")

    # Worker Group
    worker_group = parser.add_argument_group("Workers")
    worker_group.add_argument("-w", "--workers", type=int, default=1, help="The number of worker processes.")
    worker_group.add_argument("-k", "--worker-class", default="sync", choices=["sync", "gthread", "asgi", "gevent", "tornado", "gtornado"],
                             help="The type of workers to use.")
    worker_group.add_argument("--threads", type=int, default=1, help="The number of worker threads.")
    worker_group.add_argument("--worker-connections", type=int, default=1000, help="Max simultaneous clients.")
    worker_group.add_argument("--max-requests", type=int, default=0, help="Max requests before worker restart.")
    worker_group.add_argument("--max-requests-jitter", type=int, default=0, help="Jitter for max-requests.")
    worker_group.add_argument("-t", "--timeout", type=int, default=30, help="Worker timeout in seconds.")
    worker_group.add_argument("--graceful-timeout", type=int, default=30, help="Timeout for graceful restart.")
    worker_group.add_argument("--keep-alive", type=int, default=2, help="Keep-alive timeout.")
    worker_group.add_argument("--preload", action="store_true", help="Load app code before forking.")

    # Security/SSL Group
    sec_group = parser.add_argument_group("Security")
    sec_group.add_argument("--keyfile", help="SSL key file")
    sec_group.add_argument("--certfile", help="SSL certificate file")
    sec_group.add_argument("--ca-certs", help="CA certificates file")
    sec_group.add_argument("--ssl-version", type=int, default=2, help="SSL version to use.")
    sec_group.add_argument("--ciphers", help="SSL Cipher suite to use.")
    sec_group.add_argument("-u", "--user", help="Switch worker processes to run as this user.")
    sec_group.add_argument("-g", "--group", help="Switch worker process to run as this group.")
    sec_group.add_argument("-m", "--umask", type=int, default=0, help="Bit mask for file mode.")

    # Logging Group
    log_group = parser.add_argument_group("Logging")
    log_group.add_argument("--access-logfile", help="The Access log file.")
    log_group.add_argument("--error-logfile", "--log-file", help="The Error log file.")
    log_group.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error", "critical"])
    log_group.add_argument("--capture-output", action="store_true", help="Redirect stdout/stderr to error log.")
    log_group.add_argument("--access-logformat", help="The access log format.")

    # Process Group
    proc_group = parser.add_argument_group("Process")
    proc_group.add_argument("-D", "--daemon", action="store_true", help="Daemonize the process.")
    proc_group.add_argument("-p", "--pid", help="PID filename.")
    proc_group.add_argument("--chdir", help="Change directory before loading apps.")
    proc_group.add_argument("-e", "--env", action="append", help="Set environment variables.")
    proc_group.add_argument("-n", "--name", help="Process name for setproctitle.")
    proc_group.add_argument("--reload", action="store_true", help="Restart workers on code changes.")
    proc_group.add_argument("--disable-dashboard", action="store_true", help="Disable the /asteri-status dashboard.")
    proc_group.add_argument("--control-socket", help="Path to Unix domain socket for administration.")
    proc_group.add_argument("--dirty-apps", help="Config string for routing dirty apps.")
    proc_group.add_argument("--stash-address", help="Unix socket or host:port for StashServer.")
    proc_group.add_argument("--statsd-host", help="StatsD host to emit metrics.")
    proc_group.add_argument("--statsd-port", type=int, default=8125, help="StatsD port.")
    proc_group.add_argument("--statsd-prefix", default="asteri", help="StatsD prefix.")

    # HTTP limits
    http_group = parser.add_argument_group("HTTP Limits")
    http_group.add_argument("--limit-request-line", type=int, default=4094)
    http_group.add_argument("--limit-request-fields", type=int, default=100)
    http_group.add_argument("--limit-request-field_size", type=int, default=8190)

    # HTTP/2 Group
    h2_group = parser.add_argument_group("HTTP/2")
    h2_group.add_argument("--http-protocols", default="h1", help="HTTP protocols to support (e.g., h1,h2)")
    h2_group.add_argument("--http2-max-concurrent-streams", type=int, default=100)

    args = parser.parse_args()

    # 1. Load Config File if provided
    if args.config and os.path.exists(args.config):
        config_namespace = {}
        with open(args.config) as f:
            exec(f.read(), config_namespace)
        
        # Mapping to check if argument was explicitly passed on CLI
        cli_options = {
            "workers": ["-w", "--workers"],
            "worker_class": ["-k", "--worker-class"],
            "threads": ["--threads"],
            "worker_connections": ["--worker-connections"],
            "max_requests": ["--max-requests"],
            "max_requests_jitter": ["--max-requests-jitter"],
            "timeout": ["-t", "--timeout"],
            "graceful_timeout": ["--graceful-timeout"],
            "keep_alive": ["--keep-alive"],
            "preload": ["--preload"],
            "keyfile": ["--keyfile"],
            "certfile": ["--certfile"],
            "ca_certs": ["--ca-certs"],
            "ssl_version": ["--ssl-version"],
            "ciphers": ["--ciphers"],
            "user": ["-u", "--user"],
            "group": ["-g", "--group"],
            "umask": ["-m", "--umask"],
            "access_logfile": ["--access-logfile"],
            "error_logfile": ["--error-logfile", "--log-file"],
            "log_level": ["--log-level"],
            "capture_output": ["--capture-output"],
            "access_logformat": ["--access-logformat"],
            "daemon": ["-D", "--daemon"],
            "pid": ["-p", "--pid"],
            "chdir": ["--chdir"],
            "env": ["-e", "--env"],
            "name": ["-n", "--name"],
            "reload": ["--reload"],
            "disable_dashboard": ["--disable-dashboard"],
            "limit_request_line": ["--limit-request-line"],
            "limit_request_fields": ["--limit-request-fields"],
            "limit_request_field_size": ["--limit-request-field_size"],
            "http_protocols": ["--http-protocols"],
            "http2_max_concurrent_streams": ["--http2-max-concurrent-streams"],
            "backlog": ["--backlog"],
            "reuse_port": ["--reuse-port"],
            "bind": ["-b", "--bind"],
            "control_socket": ["--control-socket"],
            "dirty_apps": ["--dirty-apps"],
            "stash_address": ["--stash-address"],
            "statsd_host": ["--statsd-host"],
            "statsd_port": ["--statsd-port"],
            "statsd_prefix": ["--statsd-prefix"]
        }
        
        for key, value in config_namespace.items():
            k_lower = key.lower()
            if hasattr(args, k_lower):
                flags = cli_options.get(k_lower, [])
                passed_on_cli = any(flag in sys.argv for flag in flags)
                if not passed_on_cli:
                    setattr(args, k_lower, value)

    # Ensure bind is always a list
    if args.bind and isinstance(args.bind, str):
        args.bind = [args.bind]
    elif not args.bind:
        args.bind = ["127.0.0.1:8000"]

    # 2. Check Config
    if args.check_config or args.print_config:
        if args.print_config:
            print(f"{Colors.BOLD}Resolved Configuration:{Colors.ENDC}")
            for arg in vars(args):
                print(f"  {arg}: {getattr(args, arg)}")
        sys.exit(0)

    # 3. Handle Chdir
    if args.chdir:
        os.chdir(args.chdir)
        sys.path.insert(0, os.getcwd())

    # 4. Handle Env
    if args.env:
        for env_var in args.env:
            if "=" in env_var:
                k, v = env_var.split("=", 1)
                os.environ[k] = v

    # 5. Logging setup
    log_file = args.error_logfile
    log_level_name = (args.log_level or "info").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    setup_logging(log_file=log_file, level=log_level, capture_output=args.capture_output)
    setup_access_logging(log_file=args.access_logfile, log_format=args.access_logformat)
    
    worker_map = {
        "sync": SyncWorker,
        "gthread": GThreadWorker,
        "asgi": ASGIWorker,
        "gevent": GeventWorker,
        "tornado": TornadoWorker,
        "gtornado": TornadoWorker
    }
    
    worker_class = worker_map[args.worker_class]
    if worker_class is None:
        logger.error(f"Worker class '{args.worker_class}' is not available.")
        sys.exit(1)

    if not args.app:
        parser.print_help()
        sys.exit(1)

    # Preload
    if args.preload:
        if args.reload:
            logger.warning(f"{Colors.YELLOW}Both --reload and --preload are enabled. Changes to preloaded code will NOT be picked up.{Colors.ENDC}")
        from asteri.utils import import_app
        import_app(args.app)
    
    # Setup binds
    binds = args.bind if args.bind else ["127.0.0.1:8000"]
    
    arbiter = Arbiter(
        args.app, worker_class, 
        num_workers=args.workers, 
        binds=args.bind, 
        reload=args.reload,
        certfile=args.certfile,
        keyfile=args.keyfile,
        daemon=args.daemon,
        pidfile=args.pid,
        user=args.user,
        group=args.group,
        umask=args.umask,
        proc_name=args.name,
        timeout=args.timeout,
        backlog=args.backlog,
        reuse_port=args.reuse_port,
        threads=args.threads,
        worker_connections=args.worker_connections,
        disable_dashboard=args.disable_dashboard,
        control_socket=args.control_socket,
        dirty_apps=args.dirty_apps,
        stash_address=args.stash_address,
        statsd_host=args.statsd_host,
        statsd_port=args.statsd_port,
        statsd_prefix=args.statsd_prefix
    )
    
    logger.info(f"Booting {Colors.CYAN}{args.worker_class}{Colors.ENDC} workers...")
    
    try:
        arbiter.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
