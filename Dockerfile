FROM pytorch/pytorch:2.5.1-cuda12.1-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONWARNINGS="ignore:Unverified HTTPS request"
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN grep -v '^--index-url https://download.pytorch.org/whl/cu121' requirements.txt \
    | grep -v '^torch==' \
    > /tmp/requirements-no-torch.txt \
    && pip install --no-cache-dir -r /tmp/requirements-no-torch.txt

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
