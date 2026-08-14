import logging
import os
import sys
import io
import importlib
import re
import errno


class NonBlockingStream:
    """Wrap a stream so writes never block when the underlying pipe is full.

    Pipes (e.g. subprocess.PIPE) that are never drained fill up quickly under
    access-log traffic; a blocked write would stall SIGTERM shutdown. This
    wrapper marks the fd non-blocking and drops lines instead of hanging.
    """

    def __init__(self, stream):
        self._stream = stream
        try:
            import fcntl

            fd = stream.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except (AttributeError, OSError, ImportError, io.UnsupportedOperation):
            pass

    def write(self, data):
        try:
            return self._stream.write(data)
        except BlockingIOError:
            return len(data)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOSPC):
                return len(data)
            raise

    def flush(self):
        try:
            self._stream.flush()
        except (BlockingIOError, OSError):
            pass

    def fileno(self):
        return self._stream.fileno()

    def isatty(self):
        try:
            return self._stream.isatty()
        except (OSError, ValueError):
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


class Colors:
    PURPLE = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    WHITE = "\033[97m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


class PrettyFormatter(logging.Formatter):
    def format(self, record):
        level_color = {
            logging.INFO: Colors.GREEN,
            logging.WARNING: Colors.YELLOW,
            logging.ERROR: Colors.RED,
            logging.DEBUG: Colors.BLUE,
        }.get(record.levelno, Colors.ENDC)

        timestamp = self.formatTime(record, self.datefmt)
        super().format(record)
        # We only want to color the level and maybe the process ID
        return f"{Colors.BLUE}[{timestamp}]{Colors.ENDC} {Colors.BOLD}[{record.process}]{Colors.ENDC} {level_color}[{record.levelname}]{Colors.ENDC} {record.getMessage()}"


def setup_logging(level=logging.INFO, log_file=None, capture_output=False):
    """Set up the default logger for Asteri."""
    logger = logging.getLogger("asteri")
    logger.setLevel(level)

    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    # Console handler
    handler = logging.StreamHandler(NonBlockingStream(sys.stdout))
    formatter = PrettyFormatter(datefmt="%H:%M:%S")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # File handler if requested
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_formatter = logging.Formatter(
            "[%(asctime)s] [%(process)d] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S %z",
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        if capture_output:
            # Redirect stdout/stderr to the log file
            # We use a simple stream wrapper to avoid recursion
            class StreamToLogger:
                def __init__(self, logger_instance, log_level):
                    self.logger = logger_instance
                    self.log_level = log_level
                    self.linebuf = ""

                def write(self, buf):
                    for line in buf.rstrip().splitlines():
                        self.logger.log(self.log_level, line.rstrip())

                def flush(self):
                    pass

            sys.stdout = StreamToLogger(logger, logging.INFO)
            sys.stderr = StreamToLogger(logger, logging.ERROR)

    return logger


class NoColorFormatter(logging.Formatter):
    """Formatter that strips ANSI color codes for file logging."""

    ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    def format(self, record):
        message = super().format(record)
        return self.ANSI_ESCAPE.sub("", message)


def setup_access_logging(log_file=None, log_format=None):
    """Set up the access logger for Asteri."""
    logger = logging.getLogger("asteri.access")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Don't send to root logger

    # Allow disabling access logging for high-throughput deployments/benchmarks
    if os.environ.get("ASTERI_NO_ACCESS_LOG", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        logger.disabled = True
        return logger

    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    fmt_str = log_format or "%(message)s"

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(NoColorFormatter(fmt_str))
        logger.addHandler(file_handler)
    else:
        # Default to stdout if no file is specified
        handler = logging.StreamHandler(NonBlockingStream(sys.stdout))
        handler.setFormatter(logging.Formatter(fmt_str))
        logger.addHandler(handler)

    return logger


logger = setup_logging()
access_logger = logging.getLogger("asteri.access")


def print_banner():
    banner = f"""
        {Colors.BOLD}{Colors.PURPLE}*ASTERI*{Colors.ENDC}
         {Colors.CYAN}v3.0.0{Colors.ENDC}
    {Colors.BOLD}{Colors.CYAN}ASTERI{Colors.ENDC} {Colors.YELLOW}Web Server{Colors.ENDC}
    """
    print(banner)


def set_proctitle(title):
    """Attempt to set the process title for better visibility in ps/top."""
    try:
        import setproctitle

        setproctitle.setproctitle(f"asteri: {title}")
    except ImportError:
        # Fallback if setproctitle is not installed (dependency-free requirement)
        pass


def import_app(app_path):
    """Import application from string 'module:callable'."""
    try:
        module_path, app_name = app_path.split(":")
        sys.path.insert(0, os.getcwd())
        # Force re-import by removing from sys.modules if it exists
        if module_path in sys.modules:
            del sys.modules[module_path]
        module = importlib.import_module(module_path)
        return getattr(module, app_name)
    except Exception as e:
        logger.error(
            f"Could not import app '{Colors.BOLD}{app_path}{Colors.ENDC}': {e}"
        )
        raise e


def get_num_workers():
    """Returns a default number of workers based on CPU count."""
    return os.cpu_count() * 2 + 1


class StatsdClient:
    """A lightweight StatsD UDP client for emitting metrics."""

    def __init__(self, host: str, port: int = 8125, prefix: str = "asteri"):
        import socket

        self.host = host
        self.port = port
        self.prefix = prefix
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, payload: str):
        try:
            self.sock.sendto(payload.encode("utf-8"), (self.host, self.port))
        except OSError:
            pass

    def increment(self, metric: str, value: int = 1):
        self._send(f"{self.prefix}.{metric}:{value}|c")

    def gauge(self, metric: str, value: float):
        self._send(f"{self.prefix}.{metric}:{value}|g")

    def timing(self, metric: str, value_ms: float):
        self._send(f"{self.prefix}.{metric}:{value_ms}|ms")


def parse_proxy_protocol(data):
    """
    Parses Proxy Protocol v1 or v2 headers from the start of incoming socket data.
    Returns: (client_addr, server_addr, remaining_data) or (None, None, data)
    """
    if data.startswith(b"PROXY "):
        end_idx = data.find(b"\r\n")
        if end_idx == -1:
            return None, None, data
        header = data[:end_idx].decode("latin-1")
        remaining = data[end_idx + 2:]
        parts = header.split(" ")
        if len(parts) >= 6:
            src_ip = parts[2]
            dst_ip = parts[3]
            src_port = int(parts[4])
            dst_port = int(parts[5])
            return (src_ip, src_port), (dst_ip, dst_port), remaining

    v2_prefix = b"\r\n\r\n\x00\r\nQUIT\n"
    if data.startswith(v2_prefix):
        if len(data) < 16:
            return None, None, data
        len_val = int.from_bytes(data[14:16], byteorder="big")
        if len(data) < 16 + len_val:
            return None, None, data

        remaining = data[16 + len_val:]
        addr_family = data[13]

        if (addr_family & 0xF0) == 0x10:  # IPv4
            src_ip = ".".join(str(b) for b in data[16:20])
            dst_ip = ".".join(str(b) for b in data[20:24])
            src_port = int.from_bytes(data[24:26], byteorder="big")
            dst_port = int.from_bytes(data[26:28], byteorder="big")
            return (src_ip, src_port), (dst_ip, dst_port), remaining
        elif (addr_family & 0xF0) == 0x20:  # IPv6
            import socket

            src_ip = socket.inet_ntop(socket.AF_INET6, data[16:32])
            dst_ip = socket.inet_ntop(socket.AF_INET6, data[32:48])
            src_port = int.from_bytes(data[48:50], byteorder="big")
            dst_port = int.from_bytes(data[50:52], byteorder="big")
            return (src_ip, src_port), (dst_ip, dst_port), remaining

    return None, None, data


def make_websocket_frame(payload, opcode=1):
    """Encodes a WebSocket frame (server-to-client, unmasked)."""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    length = len(payload)
    header = bytearray()
    header.append(0x80 | opcode)
    if length <= 125:
        header.append(length)
    elif length <= 65535:
        header.append(126)
        header.extend(length.to_bytes(2, byteorder="big"))
    else:
        header.append(127)
        header.extend(length.to_bytes(8, byteorder="big"))
    return bytes(header) + payload


def parse_websocket_frame(data):
    """Parses a WebSocket frame (client-to-server, masked)."""
    if len(data) < 2:
        return None, None, data
    fin_and_opcode = data[0]
    opcode = fin_and_opcode & 0x0F
    masked_and_length = data[1]
    masked = bool(masked_and_length & 0x80)
    length = masked_and_length & 0x7F

    idx = 2
    if length == 126:
        if len(data) < 4:
            return None, None, data
        length = int.from_bytes(data[2:4], byteorder="big")
        idx = 4
    elif length == 127:
        if len(data) < 10:
            return None, None, data
        length = int.from_bytes(data[2:10], byteorder="big")
        idx = 10

    mask_key = None
    if masked:
        if len(data) < idx + 4:
            return None, None, data
        mask_key = data[idx: idx + 4]
        idx += 4

    if len(data) < idx + length:
        return None, None, data

    raw_payload = data[idx: idx + length]
    remaining = data[idx + length:]

    if mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw_payload))
    else:
        payload = raw_payload

    return opcode, payload, remaining


def build_status_html(worker_type, pid, ppid):
    """Build canonical status dashboard HTML."""
    import psutil
    import platform
    from datetime import datetime

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
                <div class="stat-value">{pid}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Parent PID</div>
                <div class="stat-value">{ppid}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Worker Type</div>
                <div class="stat-value">{worker_type}</div>
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
            Asteri Web Server v3.0.0 &bull; {datetime.now().strftime("%H:%M:%S")}
        </div>
    </div>
</body>
</html>"""
    return status_html
