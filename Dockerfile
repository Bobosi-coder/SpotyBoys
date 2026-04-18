FROM nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

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
ENV PYTHONPATH=/app
