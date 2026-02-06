# Dockerfile for ML Dashboard (Streamlit) - Hugging Face Spaces
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Set workdir
WORKDIR /code

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy app code
COPY . .

# Expose Streamlit default port
EXPOSE 7860

# Hugging Face Spaces expects 'app.py' as entrypoint
CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0"]
