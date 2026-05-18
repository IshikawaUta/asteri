# 🌟 Asteri Web Server v2.2.2

**Asteri** is a state-of-the-art, high-performance, production-ready Python web server designed with a rich and intuitive CLI argument system. It natively supports various protocols ranging from WSGI and ASGI to binary uWSGI, async WebSockets, and premium event loops like Tornado.

---

## ✨ Key Features

- **🚀 Multi-Protocol Engine**: 
  - Full compatibility with **HTTP/1.1**, **HTTP/2** (with full frame support), **HTTP/3 (QUIC)** **[NEW]**, WSGI, ASGI, uWSGI, and **ASGI WebSocket (RFC 6455)**.
  - **⚡ C-Extension Core [NEW]**: Blazing fast HTTP and uWSGI parsing written purely in C for maximum throughput and zero-copy memory efficiency (with seamless Pure-Python fallback).
- **🏗️ Diverse Worker Archetypes**:
  - `sync`: Standard robust synchronous workers.
  - `gthread`: Thread-based concurrency model.
  - `gevent`: Asynchronous greenlet-based workers for extreme scale.
  - `asgi`: Fully asynchronous modern ASGI engine (FastAPI, Starlette).
  - `tornado` / `gtornado` **[NEW]**: Native, out-of-the-box integration of Tornado's high-performance asynchronous `IOLoop` and `HTTPServer` without application-level overhead.
- **🛡️ Advanced Security & Proxying**:
  - **Proxy Protocol (v1 & v2)**: Preserves original client IP & port details perfectly behind load balancers (Nginx, HAProxy).
  - **Systemd Socket Activation**: Smooth socket inheritance from systemd for zero-downtime, rolling application deployments.
  - **HTTP 103 Early Hints**: Pre-streams Link preload headers to the client before response bodies are fully generated, optimizing page load times.
- **🌐 Inter-Process Communication (IPC)**:
  - **Control Socket (Unix Domain Socket Admin)**: A dedicated admin channel to scale workers, check status, reload code, or stop the cluster at runtime.
  - **Dirty Apps Dynamic Routing**: Routes different WSGI/ASGI apps dynamically based on the HTTP Host header or URL path prefix.
  - **Stash Server**: A lightning-fast, atomic, thread-safe, cross-process binary key-value memory store for sharing state across workers.
- **📊 Production Monitoring & Observability**:
  - **Prometheus & OpenTelemetry [NEW]**: Native Prometheus `0.0.4` metric exposition endpoint (`/metrics`) built right in.
  - **StatsD Integration**: Non-blocking UDP metrics collection (request counters, worker births/deaths).
  - **Premium Status Dashboard**: Real-time cluster health, resource metrics (CPU/RAM), and worker telemetry rendered in a gorgeous glassmorphism UI at `/asteri-status`. Can be disabled using CLI flags.
  - **Colorized Access Logs**: Beautiful terminal logs with dynamic HSL colored HTTP response status codes.
- **💎 Enterprise Quality & CI/CD [NEW]**:
  - **100% Type-Safe**: Enforced static typing with `Mypy` for zero runtime type-errors.
  - **Clean Architecture**: 100% PEP-8 compliant, strictly analyzed via `Ruff`.
  - **Automated PyPI Publishing**: Secure OIDC Trusted Publishing workflows via GitHub Actions.

---

## 📊 Performance Benchmark

Asteri is built for speed and extreme throughput. In local rigorous concurrency benchmarks, Asteri outperformed Gunicorn and Uvicorn in throughput, request-per-second, and overall system latency.

| Server Engine | Protocol Type | RPS (Requests Per Second) | Latency (ms) |
|---|---|---|---|
| 🌟 **Asteri (GTornado)** | **WSGI** | **32.60** | **1533.55** |
| 🌟 **Asteri (Tornado)** | **WSGI** | **32.01** | **1561.90** |
| 🌟 **Asteri (GThread)** | **WSGI** | **31.94** | **1565.61** |
| 🌟 **Asteri (ASGI)** | **ASGI** | **30.78** | **1624.66** |
| Asteri (Gevent) | WSGI | 30.73 | 1627.05 |
| Asteri (Sync) | WSGI | 30.65 | 1631.20 |
| Gunicorn (Sync) | WSGI | 30.50 | 1639.27 |
| Uvicorn | ASGI | 23.09 | 2165.76 |

*\*Benchmark conducted with 1,000 requests and 50 concurrent connections.*

---

## 🚀 Installation

Install Asteri in development/local mode with all core components:
```bash
git clone https://github.com/IshikawaUta/asteri.git
cd asteri
pip install -e .
```

---

## 🛠️ Basic Usage

Spin up a simple WSGI application:
```bash
asteri myapp:app
```

Run with 4 worker processes and bind to multiple network interfaces:
```bash
asteri myapp:app -w 4 -b 127.0.0.1:8000 -b 127.0.0.1:8001
```

---

## 📚 Rich Showcases & Examples

Asteri ships with multiple beautifully styled example applications illustrating standard and advanced integrations.

### 🍃 Flask (WSGI)
Run a thread-safe multi-worker Flask application:
```bash
python3 -m asteri example_flask:app -k gthread -w 4 -b 127.0.0.1:8000
```

### ⚡ FastAPI (ASGI)
Run a high-concurrency modern async FastAPI application:
```bash
python3 -m asteri example_fastapi:app -k asgi -w 4 -b 127.0.0.1:8000
```

### 🌪️ Tornado & GTornado (WSGI) [NEW]
Run standard WSGI apps wrapped in Tornado's asynchronous HTTP server container. The Asteri status dashboard `/asteri-status` and request logging are natively intercepted inside the core worker!
```bash
python3 -m asteri example_tornado:app -k tornado -w 4 -b 127.0.0.1:8000
```
Or use the Greenlet-enabled GTornado worker:
```bash
python3 -m asteri example_tornado:app -k gtornado -w 4 -b 127.0.0.1:8000
```

### 💎 Advanced ASGI Showcase
Demonstrates advanced real-time features like **bidirectional WebSockets**, **dynamic atomic Stash shared state**, and **Proxy Protocol IP extraction** on a beautifully integrated premium dashboard:
```bash
python3 -m asteri example_advanced:app -k asgi -w 4 -b 127.0.0.1:8000
```

### 🌐 uWSGI + Nginx
Serve uWSGI binary socket connections natively by binding directly to uWSGI endpoints:
```bash
python3 -m asteri example_wsgi:app -b 127.0.0.1:8000
```

---

## 📖 Complete CLI Reference

Asteri exposes a professional-grade set of configuration options via the command-line interface.

### 🌐 Network Configuration
*   `-b, --bind ADDRESS`: Bind address in socket format (e.g. `127.0.0.1:8000`). Can be specified multiple times to listen on multiple sockets.
*   `--backlog INT`: Maximum size of the pending connection queue (default: `2048`).
*   `--reuse-port`: Sets the `SO_REUSEPORT` socket option to enable high-efficiency multi-process load balancing at the kernel level.

### 👷 Workers & Performance
*   `-w, --workers INT`: Number of worker processes (default: `1`).
*   `-k, --worker-class STRING`: Worker model to run (`sync`, `gthread`, `asgi`, `gevent`, `tornado`, `gtornado`).
*   `--threads INT`: Concurrency threads limit per worker (for `gthread` worker class).
*   `--worker-connections INT`: Max simultaneous client connections per worker process (default: `1000`).
*   `-t, --timeout INT`: Maximum seconds a worker can run without refreshing its heartbeat before getting forcefully restarted (default: `30`).
*   `--graceful-timeout INT`: Timeout window in seconds for workers to complete active requests during shutdown/restart (default: `30`).
*   `--keep-alive INT`: Keep-Alive socket timeout in seconds (default: `2`).
*   `--max-requests INT`: Force workers to restart cleanly after serving `N` requests to prevent memory leaks (default: `0` / disabled).
*   `--max-requests-jitter INT`: Random jitter offset to add to `max-requests` to prevent all workers restarting at the same moment.
*   `--preload`: Preload application code into the master Arbiter process before spawning children to share memory via Copy-On-Write.

### 🔒 Security & SSL
*   `--certfile FILE`: SSL certificate chain file.
*   `--keyfile FILE`: SSL private key file.
*   `--ca-certs FILE`: Trusted CA certificates file.
*   `--ssl-version INT`: SSL/TLS protocol version constraint to use.
*   `--ciphers STRING`: Allowed SSL Cipher suites.
*   `-u, --user USER`: Run workers as a specific Unix user account.
*   `-g, --group GROUP`: Run workers as a specific Unix group.
*   `-m, --umask INT`: Bit mask for file mode creation.

### 📝 Logging & Diagnostics
*   `--access-logfile FILE`: Output path for the access log.
*   `--error-logfile FILE` (or `--log-file`): Output path for the system error log.
*   `--log-level LEVEL`: Log level verbosity (`debug`, `info`, `warning`, `error`, `critical`).
*   `--access-logformat STRING`: Customize the access log pattern structure.
*   `--capture-output`: Intercept and capture worker stdout/stderr and redirect them to the error log.
*   `--check-config`: Thoroughly validate all configurations and immediately exit.
*   `--print-config`: Dump final parsed configuration parameters to terminal and exit.

### ⚙️ Process Cluster Management
*   `-D, --daemon`: Run the Asteri cluster asynchronously in the background.
*   `-p, --pid FILE`: Path to write the master Arbiter process ID file.
*   `-n, --name STRING`: Custom process title description for process managers (`top`, `htop`, `ps`).
*   `-e, --env NAME=VALUE`: Inject environment variables into child workers.
*   `--reload`: Watch the workspace for code changes and automatically hot-reload workers.
*   `--chdir DIR`: Change working directory before loading applications.
*   `--disable-dashboard`: Completely disable the `/asteri-status` monitoring dashboard route.

### 🚀 IPC & Advanced Customizations
*   `--control-socket FILE`: Path to a Unix domain socket to expose the cluster admin control panel.
*   `--dirty-apps STRING`: Config string containing dynamic Host/path routing mappings.
*   `--stash-address STRING`: Unix socket path or `host:port` configuration of the active StashServer.
*   `--statsd-host STRING`: Hostname of the target StatsD metrics server.
*   `--statsd-port INT`: Port of the target StatsD server (default: `8125`).
*   `--statsd-prefix STRING`: Prefix name namespace for StatsD metrics (default: `asteri`).

### 📐 HTTP Limits & Protocols
*   `--limit-request-line INT`: Max allowed bytes in an HTTP request line (default: `4094`).
*   `--limit-request-fields INT`: Max number of HTTP headers allowed in a request (default: `100`).
*   `--limit-request-field_size INT`: Max size in bytes of a single HTTP header field (default: `8190`).
*   `--http-protocols STRING`: HTTP protocol standard constraints (e.g. `h1,h2,h3`).
*   `--http2-max-concurrent-streams INT`: Limit on concurrent streams for HTTP/2.

---

## ⚙️ Configuration File

For enterprise-grade setups, you can define your configuration in a Python file:

```python
# asteri.conf.py
bind = ["127.0.0.1:8080", "127.0.0.1:8081"]
workers = 4
worker_class = "gthread"
timeout = 60
reload = True
```

Run with the config file:
```bash
asteri myapp:app -c asteri.conf.py
```

---

## 🧪 Running Tests

Asteri utilizes standard Python `unittest` modules alongside automated script-based regression testing to guarantee codebase stability.

### 1. Run Core Unit Test Suite
Run unit tests with the `--buffer` flag to keep output clean from test server logs:
```bash
python3 -m unittest discover -s tests --buffer
```

### 2. Run CLI Regression Bash Suite
Test all 36+ CLI argument configurations, daemon handling, and worker scaling dynamically:
```bash
./test_asteri_cli.sh
```

---

## 📜 License
This project is licensed under the terms of the **MIT License**.