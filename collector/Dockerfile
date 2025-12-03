FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 1. Set the working directory container
WORKDIR /app

# 2. Copy requirements first (Docker caches this layer!)
COPY requirements.txt ./collector/requirements.txt

# 3. Install dependencies
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=200 -r collector/requirements.txt

# 4. Copy the source code
COPY . ./collector

# 5. Set the working directory to the app folder
ENV PYTHONPATH=/app

# 6. Default command: Run the scraper
CMD ["python", "./collector/yt_scraper.py"]