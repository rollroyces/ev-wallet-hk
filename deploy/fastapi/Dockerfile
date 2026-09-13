# =============================================================================
# EV Wallet HK — FastAPI service image
# Multi-stage, non-root, signal-aware for graceful shutdown of WS sessions.
# =============================================================================

# ---- Build stage ----------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Build deps for psycopg / cryptography
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage --------------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root user with FIXED UID so file ownership is predictable across mounts
RUN groupadd --system --gid 1000 evw \
    && useradd  --system --uid 1000 --gid evw --create-home --shell /bin/bash evw

WORKDIR /app

# Install only runtime libraries (not dev headers)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 tini curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application code (paths will be filled when FastAPI is added)
# COPY --chown=evw:evw src/ ./src/

USER evw

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/usr/local/bin:$PATH

EXPOSE 8000

# tini as PID1 — forwards signals and reaps zombies. CRITICAL for WS graceful
# shutdown: uvicorn receives SIGTERM, drains in-flight WebSockets, exits cleanly.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Healthcheck: same as compose, so container-level health is consistent.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# NOTE: command is provided by docker-compose (overrides ENTRYPOINT arg).
# Keeping it here as a sane default for `docker run` outside compose:
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "172.16.0.0/12,10.0.0.0/8", \
     "--log-level", "info", \
     "--access-log"]