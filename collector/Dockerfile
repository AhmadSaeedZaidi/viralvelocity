# 1. Use a slim Python image (lightweight & fast)
FROM python:3.11-slim

# 2. Prevent Python from writing .pyc files (useless in containers)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 3. Set the working directory container
WORKDIR /app

# 4. Copy requirements first (Docker caches this layer!)
COPY requirements.txt .

# 5. Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy the source code
COPY . .

# 7. Default command: Run the scraper
CMD ["python", "yt_scraper.py"]