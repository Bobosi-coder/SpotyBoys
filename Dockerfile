# Use an official lightweight Python runtime
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies (specifically FFmpeg for audio processing)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (Note: data/ and models/ should be mounted via volumes to avoid large image sizes)
COPY src/ ./src/

# By default, run the master pipeline script
ENTRYPOINT ["python", "src/run_all_pipelines.py"]
