# ── Stage 1: dependency builder ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile some Python packages (psycopg2, cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
# Install deps into an isolated prefix so we can copy just the site-packages
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir .


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="EvidentRx API" \
      org.opencontainers.image.description="340B Compliance Intelligence — FastAPI service" \
      org.opencontainers.image.version="9.0.0"

# Non-root user for least-privilege execution
RUN groupadd --gid 1001 evidentrx \
 && useradd  --uid 1001 --gid evidentrx --no-create-home --shell /bin/false evidentrx

# Runtime system deps (libpq for psycopg2, libssl for cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libssl3 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY --chown=evidentrx:evidentrx . .

# Drop to non-root
USER evidentrx

EXPOSE 8000

# Health-check used by Docker and Kubernetes liveness probes
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Entrypoint via shell script for pre-flight checks
ENTRYPOINT ["deploy/entrypoint.sh"]
CMD ["api"]
