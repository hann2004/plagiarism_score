# Use official Python 3.12 slim base image
FROM python:3.12-slim

# Install Java JRE (default-jre-headless), wget, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    wget \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Set project working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Create jplag directory and download JPlag executable jar if missing
RUN mkdir -p /app/backend/jplag && \
    wget -q -O /app/backend/jplag/jplag.jar https://github.com/jplag/JPlag/releases/download/v5.0.0/jplag-5.0.0-jar-with-dependencies.jar

# Copy repository source code
COPY . /app/

# Expose container port (Render assigns $PORT dynamically)
EXPOSE 8000

# Switch working directory to backend
WORKDIR /app/backend

# Launch FastAPI app with Uvicorn
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
