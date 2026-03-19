# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- Stage 2: Runtime ---
FROM python:3.12-slim

WORKDIR /app

# Copy only installed packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY . .

# Playwright browser install (for SOTA sniffer)
RUN playwright install chromium --with-deps || true

# Create non-root user
RUN useradd -m proxyuser
USER proxyuser

EXPOSE 8080 9090

# Health check
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "main.py"]
