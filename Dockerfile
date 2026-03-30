FROM python:3.12-slim

WORKDIR /app

# Non-root user
RUN groupadd -r llmproxy && useradd -r -g llmproxy llmproxy

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Supply chain verification: scan for malicious .pth files post-install
# Defense against litellm-style attacks (2026-03-24)
RUN echo "=== .pth file audit ===" && \
    SITE_DIR=$(python -c 'import site; print(site.getsitepackages()[0])') && \
    if find "$SITE_DIR" -name "*.pth" -exec grep -lE "(exec\(|eval\(|subprocess|Popen|__import__|urllib|socket)" {} \; | grep -q .; then \
        echo "CRITICAL: Suspicious .pth file detected!" && exit 1; \
    else \
        echo "Clean: no malicious .pth files found"; \
    fi

# Copy application source
COPY . .
RUN chown -R llmproxy:llmproxy /app

USER llmproxy

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["python", "-u", "main.py"]
