# Builder stage: compile the C extension and build the wheel
FROM python:3.11-bookworm AS builder

WORKDIR /build

COPY . .

RUN pip install --upgrade pip \
 && pip install build \
 && python -m build --wheel

# Runtime stage: minimal image, wheel only
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl \
 && rm -f /tmp/*.whl \
 && adduser --disabled-password --gecos "" asteri \
 && mkdir -p /app && chown asteri:asteri /app

USER asteri

EXPOSE 8000

ENTRYPOINT ["asteri"]
CMD ["--help"]
