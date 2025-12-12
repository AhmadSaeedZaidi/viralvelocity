# YouTube ML Microservice API

This is the inference backend for the **ViralVelocity** project. It hosts 6 lightweight Machine Learning models using FastAPI, optimized for deployment on Hugging Face Spaces (Free Tier).

## 🧠 Models Hosted

1.  **Velocity Predictor (XGBoost)**: Forecasts view counts 7 days out.
2.  **Clickbait Detector (Random Forest)**: Classifies videos based on engagement ratios.
3.  **Genre Classifier (PCA + MLP)**: Categorizes video content from metadata.
4.  **Tag Recommender (Association Rules)**: Suggests optimized tags.
5.  **Viral Trend Classifier (Logistic Regression)**: Predicts "Trending" status.
6.  **Anomaly Detector (Isolation Forest)**: Identifies manipulated statistics.

## 🛠️ Tech Stack

-   **Framework**: FastAPI
-   **ML Libraries**: Scikit-Learn, XGBoost
-   **Validation**: Pydantic v2
-   **Deployment**: Docker (Hugging Face Spaces)

## 🚀 Getting Started

### Local Development

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Environment Variables**:
    Copy `.env.example` to `.env` and configure your settings.
    ```bash
    cp .env.example .env
    ```

3.  **Run the Server**:
    ```bash
    uvicorn app.main:app --reload
    ```
    Access the Swagger UI at `http://localhost:8000/docs`.

4.  **Run Tests**:
    ```bash
    pytest tests/
    ```

### Docker (Local)

Build and run the container:

```bash
docker build -t youtube-ml-api .
docker run -p 7860:7860 youtube-ml-api
```

Access at `http://localhost:7860/docs`.

## ☁️ Deployment on Hugging Face Spaces

1.  Create a new Space on Hugging Face.
2.  Select **Docker** as the SDK.
3.  Push this entire repository to the Space.
4.  (Optional) Go to **Settings > Variables** in your Space to set environment variables like `DEBUG=False`.

## ⚙️ Configuration

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ENABLE_MOCK_INFERENCE` | `True` | If `True`, returns dummy data when model files (`.pkl`) are missing. Set to `False` in production after training pipeline runs. |
| `MODEL_DIR` | `models_storage` | Directory where `.pkl` files are looked for. |
