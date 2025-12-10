FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./collector/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=200 -r collector/requirements.txt
COPY . ./collector
ENV PYTHONPATH=/app
CMD ["python", "./collector/main.py"]