FROM python:3.12-slim

WORKDIR /app

# Non-root user
RUN groupadd -r llmproxy && useradd -r -g llmproxy llmproxy

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .
RUN chown -R llmproxy:llmproxy /app

USER llmproxy

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["python", "-u", "main.py"]
