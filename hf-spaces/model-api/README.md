# YouTube ML Microservice

This microservice provides machine learning predictions for YouTube video analytics, including velocity prediction, genre classification, and more.

## Features

- **Velocity Prediction**: Predicts the growth trajectory of videos.
- **Genre Classification**: Classifies video content into genres.
- **Mock Inference**: Supports mock inference for testing without heavy model files.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Environment Variables**:
    Copy `.env.example` to `.env` and configure your settings.
    ```bash
    cp .env.example .env
    ```

3.  **Run the API**:
    ```bash
    uvicorn app.main:app --reload
    ```

## Docker

Build and run the container:

```bash
docker build -t youtube-ml-api .
docker run -p 7860:7860 youtube-ml-api
```

## API Documentation

Once running, visit `http://localhost:7860/docs` for the interactive API documentation.
