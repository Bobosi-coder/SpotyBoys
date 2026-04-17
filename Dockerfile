FROM nvidia/cuda:12.1.0-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    curl git awscli python3-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# Install Python dependencies (cached layer)
COPY pyproject.toml uv.lock* ./
RUN uv sync

# Install ROCm-compatible or CUDA PyTorch depending on what uv sync gives us
# (uv sync handles torch>=2.1.0 with CUDA extras from pyproject.toml)

# Copy source code and scripts
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY experiment_sweep.sh ./

# Pre-create artifact directories (volumes will overlay these at runtime)
RUN mkdir -p \
    artifacts/item2vec \
    artifacts/retriever/split \
    artifacts/retriever/cooc \
    artifacts/retriever/popularity \
    artifacts/retriever/pref_nn \
    artifacts/ranker \
    logs \
    /tmp/delta

ENV PYTHONWARNINGS="ignore:Unverified HTTPS request"
