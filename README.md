# 🌟 Asteri Web Server v3.0.0

[![PyPI - Version](https://img.shields.io/pypi/v/asteri.svg)](https://pypi.org/project/asteri/)
[![Python Versions](https://img.shields.io/pypi/pyversions/asteri.svg)](https://pypi.org/project/asteri/)
[![License: MIT](https://img.shields.io/pypi/l/asteri.svg)](LICENSE)
[![Tests](https://github.com/IshikawaUta/asteri/actions/workflows/python-tests.yml/badge.svg)](https://github.com/IshikawaUta/asteri/actions/workflows/python-tests.yml)
[![codecov](https://codecov.io/gh/IshikawaUta/asteri/branch/main/graph/badge.svg)](https://codecov.io/gh/IshikawaUta/asteri)
[![CodSpeed](https://img.shields.io/endpoint?url=https://codspeed.io/badge.json)](https://app.codspeed.io/IshikawaUta/asteri?utm_source=badge)
[![Security](https://img.shields.io/badge/security-zizmor-2A6DB2)](https://docs.zizmor.sh/)
[![Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Mypy](https://img.shields.io/badge/typing-mypy-2A6DB2.svg)](https://github.com/python/mypy)

**Asteri** is a state-of-the-art, high-performance, production-ready Python web server with a rich and intuitive CLI. It natively supports **WSGI**, **ASGI**, **HTTP/1.1**, **HTTP/2**, and **HTTP/3 (QUIC)**, multiple worker archetypes, advanced process orchestration, and first-class observability — with **100% test coverage**, strict `mypy` typing, and `ruff`-clean code.

---

## ✨ Key Features

### 🚀 Multi-Protocol Engine
- Full compatibility with **HTTP/1.1**, **HTTP/2** (complete frame support), and **HTTP/3 (QUIC)**, plus **WSGI**, **ASGI**, **uWSGI**, and **ASGI WebSocket (RFC 6455)**.
- **⚡ C-Extension Core**: Blazing-fast HTTP and uWSGI parsing written in C for maximum throughput and zero-copy memory efficiency, with a seamless Pure-Python fallback.
- **HTTP/2 Keep-Alive multiplexing** with per-stream concurrency control.
- **HTTP 103 Early Hints**: pre-streams `Link` preload headers before the response body is generated, optimizing page load times.

### 🏗️ Diverse Worker Archetypes
| Worker | Model | Best for |
|---|---|---|
| `sync` | Synchronous processes | Simple, predictable workloads |
| `gthread` | Thread-based concurrency | I/O-bound WSGI apps |
| `gevent` | Async greenlets | Extreme concurrency at scale |
| `asgi` | Native async ASGI engine | FastAPI, Starlette, Quart |
| `tornado` / `gtornado` | Tornado `IOLoop`/`HTTPServer` | Tornado apps & high-performance async WSGI |

### 🛡️ Advanced Security & Proxying
- **HAProxy PROXY Protocol (v1 & v2)**: preserves the original client IP/port behind load balancers (Nginx, HAProxy) via `--proxy-protocol`.
- **TLS/SSL** with configurable certificate chains, CA bundles, protocol versions, and cipher suites.
- **Systemd Socket Activation**: inherits sockets from systemd for zero-downtime rolling deployments.
- **`--max-body-size`**: enforce an upper bound on accepted request bodies (0 = unlimited).
- **Request hardening**: configurable limits for request line length, header count, and header field size.

### 🌐 Inter-Process Communication (IPC)
- **Control Socket**: a Unix-domain admin channel to scale workers, check status, reload code, or stop the cluster at runtime.
- **Dirty Apps Dynamic Routing**: routes different WSGI/ASGI apps by HTTP `Host` header or URL path prefix.
- **Stash Server**: a fast, atomic, thread-safe, cross-process binary key-value store for sharing state across workers.

### 📊 Production Monitoring & Observability
- **Prometheus `/metrics` endpoint** (native `0.0.4` exposition format); disable with `--disable-metrics`.
- **StatsD integration**: non-blocking UDP metrics (request counters, worker births/deaths).
- **Premium status dashboard** at `/asteri-status` — real-time cluster health, CPU/RAM, and worker telemetry in a glassmorphism UI; disable with `--disable-dashboard`.
- **Colorized access logs** with dynamic HSL-colored HTTP status codes.

### 💎 Enterprise Quality & CI/CD
- **100% test coverage** (2779/2779 statements) across Python 3.8 → 3.13.
- **100% type-safe**: enforced static typing with `mypy` (clean on 52 source files).
- **`ruff`-clean**, PEP 8 compliant codebase.
- Fully automated GitHub Actions pipeline: unit + coverage, CodSpeed performance tracking, `zizmor` supply-chain security analysis, GitHub/GHCR/PyPI releases.

---

## 📊 Performance Benchmark

Local rigorous concurrency benchmark (median of 5 runs, 8,000 requests, 50 concurrent connections, no keep-alive):

| Server Engine | Protocol | RPS (Requests/s) | Latency (ms) |
|---|---|---|---|
| 🌟 **Asteri (ASGI)** | **ASGI** | **2,428.22** | **20.59** |
| Uvicorn | ASGI | 2,150.76 | 23.25 |
| 🌟 **Asteri (Sync)** | **WSGI** | **1,114.48** | **44.86** |
| 🌟 **Asteri (GThread)** | **WSGI** | **738.45** | **67.71** |
| Gunicorn (Sync) | WSGI | 679.84 | 73.55 |
| 🌟 **Asteri (Gevent)** | **WSGI** | **585.58** | **85.39** |
| 🌟 **Asteri (GTornado)** | **WSGI** | **580.37** | **86.15** |
| 🌟 **Asteri (Tornado)** | **WSGI** | **559.25** | **89.41** |

Asteri's native ASGI engine leads the pack in throughput and latency; every WSGI archetype outpaces Gunicorn.

### ⏱️ Continuous micro-benchmarks

Every push and pull request runs the `benchmarks/` suite on [CodSpeed](https://app.codspeed.io/IshikawaUta/asteri) in CPU simulation mode, so performance regressions on the request hot paths are caught before they are merged. The suite covers HTTP/1.1 parsing (C extension **and** pure-Python fallback), header handling and limits, chunked/content-length body framing, response building, HTTP/3 QPACK + QUIC packet handling, the uWSGI binary protocol, PROXY protocol v1/v2, WebSocket framing, the Stash TLV codec, and Dirty app routing.

Run them locally:
```bash
pip install -e . pytest pytest-codspeed
pytest benchmarks/ --codspeed
```

---

## 🚀 Installation

### From PyPI
```bash
pip install asteri
```

### Development / local install (with C-extension)
```bash
git clone https://github.com/IshikawaUta/asteri.git
cd asteri
pip install -e .
```

> The C-extension (`asteri.fastparser`) is compiled automatically when a compiler is available; otherwise Asteri falls back to a pure-Python parser.

---

## 🛠️ Basic Usage

Spin up a simple WSGI application:
```bash
asteri myapp:app
```

Run with 4 worker processes and bind to multiple interfaces:
```bash
asteri myapp:app -w 4 -b 127.0.0.1:8000 -b 127.0.0.1:8001
```

Serve an ASGI app (FastAPI/Starlette):
```bash
asteri myapp:app -k asgi -w 4
```

Check version / validate config:
```bash
asteri --version
asteri myapp:app --check-config
asteri myapp:app --print-config
```

---

## 📚 Examples

Asteri ships with several styled example applications under the repo root.

### 🍃 Flask (WSGI)
```bash
python3 -m asteri example_flask:app -k gthread -w 4 -b 127.0.0.1:8000
```

### ⚡ FastAPI (ASGI)
```bash
python3 -m asteri example_fastapi:app -k asgi -w 4 -b 127.0.0.1:8000
```

### 🌪️ Tornado & GTornado (WSGI)
The status dashboard and request logging are natively intercepted inside the core worker.
```bash
python3 -m asteri example_tornado:app -k tornado -w 4 -b 127.0.0.1:8000
python3 -m asteri example_tornado:app -k gtornado -w 4 -b 127.0.0.1:8000
```

### 💎 Advanced ASGI Showcase
Bidirectional **WebSockets**, atomic **Stash** shared state, and **Proxy Protocol** IP extraction on the dashboard:
```bash
python3 -m asteri example_advanced:app -k asgi -w 4 -b 127.0.0.1:8000
```

### 🌐 uWSGI / WSGI
```bash
python3 -m asteri example_wsgi:app -b 127.0.0.1:8000
```

---

## 📖 Complete CLI Reference

Asteri exposes a professional-grade set of options.

### ⚙️ Config
* `-c, --config FILE`: Load a Python configuration file.
* `-v, --version`: Show version and exit.
* `--check-config`: Validate the configuration and exit.
* `--print-config`: Dump the final parsed configuration and exit.

### 🌐 Network
* `-b, --bind ADDRESS`: Socket to bind (e.g. `127.0.0.1:8000`). Repeatable.
* `--backlog INT`: Maximum pending connections (default: `2048`).
* `--reuse-port`: Set `SO_REUSEPORT` for kernel-level multi-process load balancing.
* `--proxy-protocol`: Accept HAProxy PROXY protocol (v1/v2) on incoming connections. Use only behind a trusted load balancer.

### 👷 Workers
* `-w, --workers INT`: Number of worker processes (default: `1`).
* `-k, --worker-class STRING`: `sync`, `gthread`, `asgi`, `gevent`, `tornado`, `gtornado`.
* `--threads INT`: Threads per worker (default: `1`).
* `--worker-connections INT`: Max simultaneous clients per worker (default: `1000`).
* `-t, --timeout INT`: Worker heartbeat timeout in seconds (default: `30`).
* `--graceful-timeout INT`: Graceful restart window in seconds (default: `30`).
* `--keep-alive INT`: Keep-alive timeout in seconds (default: `2`).
* `--max-requests INT`: Restart workers after N requests (default: `0` / disabled).
* `--max-requests-jitter INT`: Jitter added to `max-requests` to stagger restarts.
* `--preload`: Load the app before forking to share memory via Copy-On-Write.

### 🔒 Security & SSL
* `--certfile FILE`, `--keyfile FILE`: TLS certificate chain and private key.
* `--ca-certs FILE`: Trusted CA certificates file.
* `--ssl-version INT`: SSL/TLS protocol version constraint.
* `--ciphers STRING`: Allowed cipher suites.
* `-u, --user USER`, `-g, --group GROUP`: Drop worker privileges.
* `-m, --umask INT`: File-mode creation mask.

### 📝 Logging
* `--access-logfile FILE`: Access log output path.
* `--error-logfile FILE` / `--log-file FILE`: Error log output path.
* `--log-level LEVEL`: `debug`, `info`, `warning`, `error`, `critical`.
* `--access-logformat STRING`: Customize the access log pattern.
* `--capture-output`: Redirect worker stdout/stderr to the error log.

### ⚙️ Process Management
* `-D, --daemon`: Daemonize the master process.
* `-p, --pid FILE`: Write the master PID file.
* `-n, --name STRING`: Custom process title for `ps`/`top`/`htop`.
* `-e, --env NAME=VALUE`: Inject environment variables into workers.
* `--reload`: Hot-reload workers on code changes.
* `--chdir DIR`: Change working directory before loading apps.
* `--disable-dashboard`: Disable the `/asteri-status` dashboard.
* `--disable-metrics`: Disable the Prometheus `/metrics` endpoint.
* `--max-body-size INT`: Maximum accepted request body in bytes (default: `0` = unlimited).

### 🚀 IPC & Advanced
* `--control-socket FILE`: Unix-domain admin socket.
* `--dirty-apps STRING`: Host/path routing mappings for dynamic apps.
* `--stash-address STRING`: Unix socket or `host:port` of the StashServer.
* `--statsd-host STRING`, `--statsd-port INT`, `--statsd-prefix STRING`: StatsD metrics target (default port `8125`, prefix `asteri`).

### 📐 HTTP Limits & Protocols
* `--limit-request-line INT`: Max request-line bytes (default: `4094`).
* `--limit-request-fields INT`: Max headers per request (default: `100`).
* `--limit-request-field_size INT`: Max bytes per header field (default: `8190`).
* `--http-protocols STRING`: Protocol set, e.g. `h1,h2,h3` (default: `h1`).
* `--http2-max-concurrent-streams INT`: Max concurrent HTTP/2 streams (default: `100`).

---

## ⚙️ Configuration File

For enterprise setups, define the configuration in a Python file:

```python
# asteri.conf.py
bind = ["127.0.0.1:8080", "127.0.0.1:8081"]
workers = 4
worker_class = "gthread"
timeout = 60
reload = True
proxy_protocol = True
max_body_size = 1048576
```

Run with the config file:
```bash
asteri myapp:app -c asteri.conf.py
```

> ⚠️ Config files execute arbitrary Python code — only use files from trusted sources.

---

## 🐳 Docker

A multi-stage, non-root Docker image is available:

```bash
docker build -t ghcr.io/ishikawauta/asteri:latest .
docker run --rm -p 8000:8000 ghcr.io/ishikawauta/asteri:latest myapp:app
```

Images are published to GHCR for `amd64` and `arm64` on every version tag.

---

## 🧪 Testing & CI/CD

### Local test suite (100% coverage)
```bash
pip install -e .[test]   # or: pip install pytest pytest-cov coverage ruff mypy
pytest tests/ -q --cov=asteri --cov-report=term-missing
ruff check .
mypy asteri tests
```

### CLI regression suite
```bash
./test_asteri_cli.sh
```

### Continuous integration (GitHub Actions)
- **`python-tests.yml`** — matrix Python 3.8–3.13: `ruff`, `mypy`, `pytest --cov` (coverage uploaded to Codecov), full CLI regression suite.
- **`codspeed.yml`** — CodSpeed performance analysis on every push/PR (`benchmarks/`).
- **`zizmor.yml`** — supply-chain security audit of all workflows (weekly + on push/PR).
- **`release.yml`** — GitHub Release with changelog-driven notes on version tags.
- **`docker.yml`** — GHCR image build & push (multi-arch).
- **`publish-pypi.yml`** — PyPI publish via OIDC Trusted Publishing on version tags.

---

## 🤝 Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for the full
development setup, quality gate (`ruff` + `mypy` + 100% coverage), and PR checklist.
Project history is in [CHANGELOG.md](CHANGELOG.md).

---

## 🛡️ Security

Asteri takes supply-chain and runtime security seriously: all workflows are
audited with `zizmor`, PyPI releases use OIDC Trusted Publishing, Docker images
run unprivileged, and runtime hardening knobs (PROXY protocol, body-size and
parsing limits, privilege dropping) are built in.

To report a vulnerability, use [SECURITY.md](SECURITY.md) — please file a
private advisory at <https://github.com/IshikawaUta/asteri/security/advisories>
rather than a public issue.

---

## 📜 License

This project is licensed under the terms of the **MIT License**. See [LICENSE](LICENSE) for details.
