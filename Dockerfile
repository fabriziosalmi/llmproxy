# 11.7: Distroless Dockerization (Zero-Bloat, Zero-Surface)
# Stage 1: Build & Compile Native Extensions
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies for C++/Rust
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    curl \
    git \
    libssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Rust for the PII engine (Phase 10)
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source and compile extensions
COPY . .
RUN python setup_cpp.py build_ext --inplace
# For Rust, assuming maturin or similar is used:
RUN cd ext_rust && maturin build --release --out ../dist && pip install ../dist/*.whl

# Stage 2: Distroless Runtime
FROM gcr.io/distroless/python3-debian11

WORKDIR /app

# Copy only the necessary site-packages and compiled binaries
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /app /app

# Set Python path to find site-packages
ENV PYTHONPATH=/usr/local/lib/python3.11/site-packages

# Expose Proxy Port
EXPOSE 8090

# Non-root user (Distroless default is non-root)
USER nonroot

# Entrypoint: Direct Python execution of main.py
ENTRYPOINT ["python", "main.py"]
