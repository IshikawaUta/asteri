# 🌟 Asteri Web Server v1.1.1

**Asteri** is a high-performance, production-ready Python web server designed with a rich and intuitive CLI argument system. Asteri supports various protocols ranging from WSGI and ASGI to binary uWSGI.

## ✨ Key Features
- **Multi-Protocol Support**: HTTP/1.1, HTTP/2 (Full Frame Support), WSGI, ASGI, and uWSGI.
- **Rich Worker Types**:
  - `sync`: Standard synchronous workers.
  - `gthread`: Thread-based workers for better concurrency.
  - `gevent`: Asynchronous workers based on greenlets (extremely high performance).
  - `asgi`: Full support for modern async applications (FastAPI, Starlette).
- **Professional Auto-Reload**: Event-driven reload system using `watchdog`.
- **Process Management**: Daemon mode, PID files, and User/Group switching.
- **Security**: Easy-to-configure SSL/TLS (HTTPS) support.
- **Premium Status Dashboard**: Real-time server health monitoring at `/asteri-status` with a modern glassmorphism UI.
- **Colored Access Logs**: Visual feedback for HTTP status codes (2xx, 4xx, 5xx).

## 📊 Performance Benchmark
Asteri is built for speed. In local benchmarks, both Asteri (Sync) and Asteri (ASGI) outperformed Gunicorn and Uvicorn in throughput and latency.

| Server Name | Type | RPS (Requests Per Second) | Latency (ms) |
|-------------|------|---------------------------|--------------|
| **Asteri (Sync)** | **WSGI** | **23.71** | **2108.39** |
| **Asteri (ASGI)** | **ASGI** | **23.47** | **2130.29** |
| Asteri (Gevent) | WSGI | 22.84 | 2189.49 |
| Uvicorn | ASGI | 22.73 | 2199.27 |
| Gunicorn (Sync) | WSGI | 22.06 | 2266.41 |

*Benchmark conducted with 1000 requests and 50 concurrent connections.*

## 🚀 Installation
```bash
git clone https://github.com/IshikawaUta/asteri.git
cd asteri
pip install -e .
```

## 🛠️ Basic Usage
Run a WSGI application:
```bash
asteri myapp:app
```

Run with 4 workers and bind to multiple ports:
```bash
asteri myapp:app -w 4 -b 127.0.0.1:8000 -b 127.0.0.1:8001
```

## 📚 Running Examples
Asteri includes several examples to get you started with different frameworks and protocols.

### Flask (WSGI)
Run with thread-based workers:
```bash
python3 -m asteri example_flask:app -k gthread -w 4 -b 127.0.0.1:8000
```

### FastAPI (ASGI)
Run with modern async workers:
```bash
python3 -m asteri example_fastapi:app -k asgi -w 4 -b 127.0.0.1:8000
```

### Production with Nginx (uWSGI)
Run Asteri in uWSGI mode (automatic detection) and use the provided `example_uwsgi_nginx.conf`:
```bash
python3 -m asteri example_wsgi:app -b 127.0.0.1:8000
```

## 📖 Complete CLI Reference

Asteri supports a wide range of arguments for professional-grade control:

### 🌐 Network
- `-b, --bind ADDRESS`: Bind to specific address (e.g., `127.0.0.1:8000`). Supports multiple binds.
- `--backlog INT`: Maximum number of pending connections (default: 2048).
- `--reuse-port`: Use the `SO_REUSEPORT` socket flag for load balancing.

### 👷 Workers & Performance
- `-w, --workers INT`: Number of worker processes.
- `-k, --worker-class STRING`: Type of workers (`sync`, `gthread`, `asgi`, `gevent`).
- `--threads INT`: Number of worker threads for `gthread` worker class.
- `--worker-connections INT`: Maximum simultaneous clients per worker.
- `-t, --timeout INT`: Worker timeout in seconds before restart (default: 30).
- `--graceful-timeout INT`: Timeout for graceful worker shutdown.
- `--keep-alive INT`: Seconds to wait for requests on a Keep-Alive connection.
- `--max-requests INT`: Restart worker after N requests (prevents memory leaks).
- `--max-requests-jitter INT`: Add random jitter to `max-requests`.
- `--preload`: Load application code before forking workers (saves memory).

### 🔒 Security & SSL
- `--certfile FILE`: Path to SSL certificate file.
- `--keyfile FILE`: Path to SSL key file.
- `--ca-certs FILE`: Path to CA certificates file.
- `--ssl-version INT`: SSL version to use (TLSv1, TLSv1.1, TLSv1.2).
- `--ciphers STRING`: SSL Cipher suite to use.
- `-u, --user USER`: Run workers as a specific user.
- `-g, --group GROUP`: Run workers as a specific group.
- `-m, --umask INT`: Bit mask for file mode.

### 📝 Logging & Debugging
- `--log-file FILE`: Path to error log file.
- `--log-level LEVEL`: Log granularity (`debug`, `info`, `warning`, `error`, `critical`).
- `--access-logfile FILE`: Path to access log file.
- `--access-logformat STRING`: Custom format for access logs.
- `--capture-output`: Redirect stdout/stderr to the error log.
- `--print-config`: Print resolved configuration and exit.

### ⚙️ Process Management
- `-D, --daemon`: Run Asteri in the background.
- `-p, --pid FILE`: Create a PID file for process tracking.
- `-n, --name STRING`: Set a custom process name (visible in `ps` or `top`).
- `-e, --env NAME=VALUE`: Set environment variables.
- `--reload`: Monitor code changes and automatically reload workers.
- `--chdir DIR`: Change working directory before loading the app.

### 🚀 HTTP/2 & Limits
- `--http-protocols STRING`: Protocols to support (e.g., `h1`, `h1,h2`).
- `--http2-max-concurrent-streams INT`: Limit concurrent H2 streams.
- `--limit-request-line INT`: Max size of HTTP request line (default: 4094).
- `--limit-request-fields INT`: Max number of header fields.
- `--limit-request-field_size INT`: Max size of a single header field.

## ⚙️ Configuration File
You can use a Python file for complex configurations:

```python
# asteri.conf.py
bind = ["127.0.0.1:8080", "127.0.0.1:8081"]
workers = 4
worker_class = "gthread"
timeout = 60
reload = True
```
Run with: `asteri myapp:app -c asteri.conf.py`

## 📊 Status Dashboard
Enable the internal dashboard to monitor worker statistics:
Visit `http://localhost:8000/asteri-status` in your browser.

## 📜 License
This project is licensed under the **MIT License**.