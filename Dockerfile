# syntax=docker/dockerfile:1.7
# ── Stage 1: console & checkout ─────────────────────────────────────────────
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python runtime (gateway, worker and signer share one image) ───
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CRYPTUS_DATA_DIR=/data CRYPTUS_FRONTEND_DIST=/app/static
RUN groupadd --system --gid 10001 cryptus && useradd --system --uid 10001 --gid cryptus --home /app cryptus
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/cryptus ./cryptus
COPY backend/cryptus_signer ./cryptus_signer
RUN pip install --upgrade pip && pip install ".[kms]"
COPY --from=frontend /src/dist /app/static
RUN mkdir -p /data && chown -R cryptus:cryptus /data
USER cryptus
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4).status==200 else 1)" || exit 1
CMD ["cryptus", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
