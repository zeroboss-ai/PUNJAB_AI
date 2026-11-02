# ==============================================================================
# Multi-stage Dockerfile for Punjab AI Terminal on Render (24/7 Live)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Build React Frontend
# ------------------------------------------------------------------------------
FROM node:20-alpine AS frontend-builder
WORKDIR /app

# Install dependencies first (layer caching)
COPY frontend/package*.json ./frontend/
RUN cd frontend && npm ci

# Copy full frontend sources and compile production bundle
COPY frontend/ ./frontend/
RUN mkdir -p terminal/web && cd frontend && npm run build

# ------------------------------------------------------------------------------
# Stage 2: Python Runtime
# ------------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Python environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ALLOW_REMOTE=yes \
    HOST=0.0.0.0 \
    PORT=10000

WORKDIR /app

# Install system dependencies (curl for healthcheck, ca-certificates for TLS, git)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Copy application backend and configuration
COPY terminal/ ./terminal/
COPY run.py config.yaml ./

# Copy compiled frontend from Stage 1 into the static dist folder
COPY --from=frontend-builder /app/terminal/web/dist/ ./terminal/web/dist/

# Create runtime persistent directories
RUN mkdir -p logs data

# Expose container port
EXPOSE 10000

# Container healthcheck for Render
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT:-10000}/api/health || exit 1

# Start the terminal server
CMD ["sh", "-c", "python run.py --host ${HOST:-0.0.0.0} --port ${PORT:-10000}"]
