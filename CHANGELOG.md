# Release History

Track the evolution of the Asteri web server.

---

## v3.0.0 (Current)
*August 14, 2026*

Asteri v3.0.0 is a major release that turns the server into a full modern application platform: complete HTTP/2 server support for both WSGI and ASGI workers, persistent HTTP/1.1 keep-alive connections with request pipelining, hardened request parsing, native PROXY protocol, and a heavily optimized request pipeline.

### 🚀 New Features

- **Full HTTP/2 Server Support**: HTTP/2 is now a real server protocol, not a canned "Hello" response. Streams are multiplexed via `h2`, dispatched to WSGI (base/sync workers) and ASGI (asgi worker) applications, with per-stream flow-control acknowledgement, `max_body_size` enforcement, and `http2_max_concurrent_streams` limits. ASGI scopes report `http_version: "2.0"`.
- **HTTP/1.1 Keep-Alive & Pipelining**: A single connection now serves multiple requests; leftover pipelined bytes are buffered and reprocessed. The socket timeout drops to `keep_alive` (default 2s) after the first request.
- **PROXY Protocol v1/v2**: Native client-address extraction, enabled via the new `--proxy-protocol` flag (previously attempted on every connection).
- **uWSGI Binary Protocol**: Full 64KB packet accumulation and parsing.
- **Streaming WSGI Responses**: Generator/iterator bodies are streamed with chunked Transfer-Encoding instead of being fully buffered.
- **ASGI/WSGI HTTPS Detection**: `wsgi.url_scheme` and ASGI `scheme` are now `https` automatically when the connection uses an SSL socket.
- **uvloop Auto-Detection**: The ASGI worker uses uvloop when installed, falling back to asyncio.
- **Gevent Monkey Patching**: `gevent.monkey.patch_all()` runs before the application is imported so the app's stdlib calls are gevent-patched from the start.
- **Mutual TLS & TLS Hardening**: `--ca-certs` now enables mutual TLS (`CERT_REQUIRED`), `--ciphers` sets the cipher suite, and `--ssl-version` maps to a minimum TLS version (TLS 1.0 – TLS 1.3).
- **Stash Atomic Counters**: New `OP_INCREMENT` operation and `StashClient.increment()` for atomic shared counters across workers.

### 🔒 Security & Request Hardening

- **Request Limits**: `max_body_size`, `limit_request_line` (4094), `limit_request_fields` (100), `limit_request_field_size` (8190), and a 32KB total header cap — violated limits return proper `400`/`413`/`431`/`501` responses.
- **Response-Splitting Protection**: New `sanitize_header_name()` strips CR/LF from header names and values across HTTP/1.1, HTTP/2, and Early Hints (103).
- **Chunked Decoding**: Robust chunked Transfer-Encoding parsing with extension/trailer handling and size-cap enforcement.
- **Malformed-Request Handling**: Invalid or negative Content-Length, truncated bodies, and unsupported Transfer-Encoding now return clean error responses instead of closing the connection.
- **Connection Limit Enforcement**: `worker_connections` caps active connections before work reaches the thread pool/accept loop; slots are always released in `finally`.

### ⚡ Performance

- **Event-Driven Accept Loop**: ASGI worker switched from poll+`sleep(0.01)` to `loop.sock_accept()` (uvicorn/libuv style), eliminating the ~50ms stall on an empty accept queue.
- **Non-Blocking Access Logging**: New `NonBlockingStream` drops log lines when the pipe is full instead of blocking, preventing stalled SIGTERM shutdowns. Set `ASTERI_NO_ACCESS_LOG=1` to disable access logging entirely for high-throughput deployments.
- **Metrics Batching & Caching**: Stash counter deltas are batched and flushed every 5 seconds; the Prometheus/OpenTelemetry `/metrics` exposition is cached for ~1 second.
- **TCP_NODELAY**: Set on accepted client sockets.
- **HTTP/3 QUIC Idle Reaping**: Idle QUIC connections are swept every 30s after a 300s connection TTL.
- **Benchmark Tool**: Warmup, three runs reporting the median, 8000 requests, and SIGTERM→SIGKILL cleanup escalation.

### 🛡️ Quality & CI/CD

- **Mypy Static Typing Enforced**: The entire codebase is type-annotated and verified clean (`mypy asteri tests` — 52 source files, zero errors).
- **Ruff Static Analysis**: Strict PEP-8 compliance enforced with zero violations.
- **100% Test Coverage**: Every line of the `asteri` package is covered by the test suite.

### ✅ Testing

- **504/504 Unit Tests** passing (up from 87 in v2.2.2).
- **100% line coverage** across all 15 `asteri` modules (2779/2779 statements).
- Test suite includes full HTTP/2 server integration, keep-alive/pipelining, PROXY protocol v1/v2, uWSGI, WebSocket frames, HTTP/3/QUIC, TLS/mTLS, systemd activation, control socket, and import-fallback scenarios.

---

## v2.2.2
*May 19, 2026*

This is a landmark release that transforms Asteri from a high-performance web server into a fully **enterprise-grade production framework**. It delivers native HTTP/3, a C-Extension performance core, integrated telemetry, and a fully enforced static quality pipeline.

### 🚀 New Features

- **Native HTTP/3 (QUIC)**: Full HTTP/3 protocol support with QPACK header compression/decompression, QUIC packet framing, and integrated handshake handling.
- **⚡ C-Extension Parsing Core**: Rewrote the HTTP/1.1 and uWSGI parsers as a native C-Extension (`asteri.fastparser`) for blazing-fast zero-copy buffer throughput. Includes seamless Pure-Python fallback.
- **Prometheus & OpenTelemetry Metrics**: Native Prometheus `0.0.4`-compliant `/metrics` endpoint. Metrics are synchronized across all worker processes via the Stash IPC server.
- **Automated PyPI Publishing**: Secure, passwordless OIDC Trusted Publishing workflow via GitHub Actions — releases to PyPI automatically on every `v*.*.*` git tag push.

### 🛡️ Quality & CI/CD

- **Mypy Static Typing Enforced**: All 41 source files are 100% type-annotated and verified. Zero runtime type-errors guaranteed.
- **Ruff Static Analysis**: Cleaned up 100+ `E701` and `E722` violations. Entire codebase is now strictly PEP-8 compliant.
- **Black Formatting**: Global PEP-8 auto-formatting applied across all 47 Python files.
- **CI Pipeline Hardened**: GitHub Actions now enforces `ruff check` and `mypy` on every push before tests run.

### ✅ Testing

- **87/87 Unit Tests** passing.
- **40/40 CLI Regression Scenarios** verified via `test_asteri_cli.sh`.

---

## v1.2.2
*May 17, 2026*

This release added massive event loop integrations (Tornado), unified visual dashboards, robust ASGI fixes, and comprehensive regression test suites.

### 🚀 New Features

- **Native Tornado Integration**: Added `tornado` and `gtornado` worker classes for high-performance non-blocking async loops.
- **Unified Premium Status Dashboard**: Created a gorgeous, centralized status page builder in glassmorphism UI for all worker classes.
- **Robust Intercept Middleware**: Automated dashboard/log routing inside Tornado workers using transparent WSGI wrapper middlewares.
- **Exhaustive Regression Suite**: Upgraded the CLI test framework to cover 100% of the 36+ CLI options and system arguments.

### 🐛 Bug Fixes

- **FastAPI/Flask ASGI Compatibility**: Fixed the ASGI `__call__` calling convention parameter count bug in modern frameworks.
- **Zero Orphaned Workers**: Fixed potential zombie process hazards during abrupt shutdowns.
- **CI/CD Integration**: Expanded GitHub Actions workflow to include full discover-based unit tests.

---

## v1.2.1
*May 16, 2026*

This is a major stability and architectural update, addressing critical bugs discovered during production stress testing.

### 🚀 New Features

- **Output Capture Support**: Added `--capture-output` to redirect `stdout` and `stderr` to error log files.
- **Dynamic Environment**: Automated resolution of `SERVER_NAME` and `SERVER_PORT` via listener socket metadata.
- **Enhanced GThread**: Better thread pool management with explicit `--threads` support.
- **Streaming Body**: Implemented `WSGIInput` for efficient, memory-safe body streaming of large requests.

### 🐛 Bug Fixes

- **Arbiter**: Fixed a critical bug where the master process exited before reaping worker processes.
- **HTTP/2**: Resolved connection preface data loss during the H2 handshake.
- **uWSGI**: Fixed parsing failures for packets exceeding 4KB.
- **Signal Handling**: Improved `SIGQUIT` and `SIGTERM` behavior for graceful shutdowns.
- **Security**: Added safety limits for large headers (up to 32KB).

---

## v1.1.1
*May 14, 2026*

Improved stability release for the initial version series.

---

## v1.0.1
*May 14, 2026*

Patch release addressing minor CLI inconsistencies and formatting.

---

## v1.0.0
*May 14, 2026*

Initial public release of the Asteri web server.

### ✨ Highlights

- **Multi-Worker Support**: Integrated Sync, GThread, ASGI, and Gevent workers.
- **Protocol Switching**: Automatic detection of HTTP/1.1, HTTP/2, and uWSGI on the same port.
- **Status Dashboard**: Built-in real-time process monitoring at `/asteri-status`.
- **Advanced CLI**: Comprehensive argument system for production tuning.