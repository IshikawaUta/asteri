import logging
import os
import sys
import importlib
import re


class Colors:
    PURPLE = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    WHITE = '\033[97m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class PrettyFormatter(logging.Formatter):
    def format(self, record):
        level_color = {
            logging.INFO: Colors.GREEN,
            logging.WARNING: Colors.YELLOW,
            logging.ERROR: Colors.RED,
            logging.DEBUG: Colors.BLUE
        }.get(record.levelno, Colors.ENDC)
        
        timestamp = self.formatTime(record, self.datefmt)
        msg = super().format(record)
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
    handler = logging.StreamHandler(sys.stdout)
    formatter = PrettyFormatter(datefmt='%H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # File handler if requested
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_formatter = logging.Formatter(
            '[%(asctime)s] [%(process)d] [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S %z'
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
                    self.linebuf = ''

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
    ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    
    def format(self, record):
        message = super().format(record)
        return self.ANSI_ESCAPE.sub('', message)

def setup_access_logging(log_file=None, log_format=None):
    """Set up the access logger for Asteri."""
    logger = logging.getLogger("asteri.access")
    logger.setLevel(logging.INFO)
    logger.propagate = False # Don't send to root logger
    
    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            
    fmt_str = log_format or '%(message)s'
    
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(NoColorFormatter(fmt_str))
        logger.addHandler(file_handler)
    else:
        # Default to stdout if no file is specified
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(fmt_str))
        logger.addHandler(handler)
    
    return logger

logger = setup_logging()
access_logger = logging.getLogger("asteri.access")

def print_banner():
    banner = f"""
        {Colors.BOLD}{Colors.PURPLE}*ASTERI*{Colors.ENDC}
         {Colors.CYAN}v1.2.1{Colors.ENDC}
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
        logger.error(f"Could not import app '{Colors.BOLD}{app_path}{Colors.ENDC}': {e}")
        raise e

def get_num_workers():
    """Returns a default number of workers based on CPU count."""
    return os.cpu_count() * 2 + 1
