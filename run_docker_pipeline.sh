#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🐳 MLOps Data Engineering Pipeline - Docker Runner"
echo "=================================================="

# 1. Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed or not in your PATH."
    echo "Please install Docker Desktop: https://www.docker.com/products/docker-desktop"
    exit 1
fi

# 2. Build the Docker Image
IMAGE_NAME="spotyboys-pipeline"
echo "🔨 Building Docker image '$IMAGE_NAME'..."
docker build -t $IMAGE_NAME .
echo "✅ Image built successfully!"

# 3. Check for Data and Models directories to mount
if [ ! -d "./data" ] || [ ! -d "./models" ]; then
    echo "⚠️ Warning: './data' or './models' directory not found in current path."
    echo "The pipeline requires these mapped directories to read checkpoints and write processed datasets."
fi

# Read limit parameter if provided (e.g. ./run_docker_pipeline.sh 100)
LIMIT_ARG=$1
if [ -z "$LIMIT_ARG" ]; then
    echo "🚀 Running FULL pipeline in container..."
else
    echo "🚀 Running pipeline in container with LIMIT = $LIMIT_ARG..."
fi

# 4. Run the Container
# We mount $(pwd)/data to /app/data so the generated CSVs persist on the host machine
# We mount $(pwd)/models to /app/models so it can read Cnn14.pth
docker run --rm -it \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/models:/app/models" \
  $IMAGE_NAME $LIMIT_ARG
