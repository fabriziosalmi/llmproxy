# syntax=docker/dockerfile:1.6
# Base images are pinned by digest so a rebuild of a given commit produces
# the same foundation. `python:3.12-slim` and `node:20-alpine` are mutable
# tags rebuilt upstream, which made the largest input to the image the one
# nobody pinned — while every GitHub Action here is SHA-pinned and enforced
# by a CI job. Dependabot's `docker` ecosystem proposes the digest bumps, so
# patching stays routine rather than becoming manual.
# ── Stage 1 — UI build ────────────────────────────────────────────────────────
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS ui-builder

WORKDIR /ui

COPY ui/package.json ui/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY ui/ ./
RUN npm run build

# ── Stage 2 — Python runtime ──────────────────────────────────────────────────
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

WORKDIR /app

# Non-root user
RUN groupadd -r llmproxy && useradd -r -g llmproxy llmproxy

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Supply chain verification: scan for malicious .pth files post-install
# Defense against litellm-style attacks (2026-03-24)
#
# pipefail matters here more than anywhere else in this file. The check is a
# pipeline, and without it the `if` sees the exit status of the LAST stage: a
# `find` that failed — unreadable directory, unexpected site-packages layout,
# an empty SITE_DIR because the `python -c` printed nothing — left grep with no
# input, grep returned 1, and the build printed "Clean" and carried on. The
# gate failed OPEN in exactly the conditions where its answer was least
# trustworthy, and the reassuring message hid it in the log.
#
# SITE_DIR is now validated before use, and the number of files examined is
# printed so a vacuous pass is visible rather than indistinguishable from a
# real one.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN echo "=== .pth file audit ===" && \
    SITE_DIR=$(python -c 'import site; print(site.getsitepackages()[0])') && \
    if [ -z "$SITE_DIR" ] || [ ! -d "$SITE_DIR" ]; then \
        echo "CRITICAL: could not resolve site-packages ('$SITE_DIR')" && exit 1; \
    fi && \
    PTH_COUNT=$(find "$SITE_DIR" -name "*.pth" | wc -l) && \
    echo "Examining $PTH_COUNT .pth file(s) in $SITE_DIR" && \
    if find "$SITE_DIR" -name "*.pth" -exec grep -lE "(exec\(|eval\(|subprocess|Popen|__import__|urllib|socket)" {} \; | grep -q .; then \
        echo "CRITICAL: Suspicious .pth file detected!" && exit 1; \
    else \
        echo "Clean: no malicious .pth files found"; \
    fi

# Copy application source (ui/dist and ui/node_modules excluded via .dockerignore)
COPY . .

# Drop the built UI bundle on top of the source tree so app_factory mounts it.
COPY --from=ui-builder /ui/dist /app/ui/dist

RUN chown -R llmproxy:llmproxy /app

USER llmproxy

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["python", "-u", "main.py"]
