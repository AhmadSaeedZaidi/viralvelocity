# 🚀 ViralVelocity Training Pipeline

This directory contains the ML training infrastructure, orchestrated by **Prefect** and validated by **Deepchecks**.

---

## 🏗 Architecture

The pipeline uses a modular **"Champion vs. Challenger"** architecture:

- **Data Loader:** Fetches training pairs (History → Future snapshots) from NeonDB.
- **Feature Engineering:** Modular logic for Time, Text, and Velocity features.
- **Training:** AutoML (`RandomizedSearchCV`) trains candidate models.
- **Validation:**
  - **Deepchecks:** Validates data integrity and model drift.
  - **Champion Logic:** Compares the new model against the current production model on Hugging Face.
- **Deployment:** If the Challenger wins, it replaces the Champion on HF Hub.

---

## 📂 Directory Structure

```
training/
├── config/                 # Configuration (Hyperparams, Model Registry)
├── evaluation/             # Metrics & Validation Logic
├── feature_engineering/    # Reusable Feature Modules
├── pipelines/              # Prefect Flows (The entry points)
├── utils/                  # DB Connectors & Discord Alerts
├── Dockerfile              # Training Environment Definition
└── requirements.txt        # Python Dependencies
```

---

## 🛠 Usage

### Running Locally (Docker)

To avoid dependency hell, **always run training inside the Docker container:**

```bash
# 1. Build
docker build -t viral-training ./training

# 2. Run a specific pipeline (e.g., Velocity)
docker run --env-file .env viral-training python training/pipelines/velocity_pipeline.py
```

---

### Adding a New Model

1. **Define requirements** in `config/model_registry.yaml`.
2. **Set hyperparameters** in `config/training_config.yaml`.
3. **Create a new flow** in `pipelines/new_model_pipeline.py`.
4. **Add it to the GitHub Actions schedule**.

---