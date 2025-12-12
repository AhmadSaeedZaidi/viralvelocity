viralvelocity/
│
├── hf-spaces/
│   ├── model-api/                    # HF Space: FastAPI Model API
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py              # FastAPI entry point
│   │   │   ├── models/              # Model wrapper classes
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py          # Base model interface
│   │   │   │   ├── velocity.py
│   │   │   │   ├── clickbait.py
│   │   │   │   ├── genre.py
│   │   │   │   ├── tags.py
│   │   │   │   ├── viral.py
│   │   │   │   └── anomaly.py
│   │   │   ├── routers/             # API endpoints
│   │   │   │   ├── __init__.py
│   │   │   │   ├── predictions.py
│   │   │   │   ├── models.py
│   │   │   │   ├── metrics.py
│   │   │   │   └── health.py
│   │   │   ├── schemas/             # Pydantic models
│   │   │   │   ├── __init__.py
│   │   │   │   ├── requests.py
│   │   │   │   └── responses.py
│   │   │   ├── core/
│   │   │   │   ├── config.py        # Settings
│   │   │   │   ├── cache.py         # Caching logic
│   │   │   │   └── exceptions.py
│   │   │   └── utils/
│   │   │       ├── features.py      # Feature engineering
│   │   │       ├── validators.py
│   │   │       └── loaders.py       # Model loading utilities
│   │   ├── tests/
│   │   │   ├── test_models.py
│   │   │   └── test_api.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── README.md
│   │   └── .env.example
│   │
│   └── ml-dashboard/                 # HF Space: Streamlit Dashboard
│       ├── app.py
│       ├── pages/
│       │   ├── 1_📊_Model_Performance.py
│       │   ├── 2_🔍_Feature_Analysis.py
│       │   ├── 3_📈_Drift_Detection.py
│       │   ├── 4_🎯_Live_Predictions.py
│       │   └── 5_⚙️_Model_Config.py
│       ├── utils/
│       │   ├── api_client.py
│       │   ├── visualizations.py
│       │   └── data_processing.py
│       ├── requirements.txt
│       └── README.md
│
├── frontend/                         # Vercel Next.js App
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                 # Landing page
│   │   ├── models/
│   │   │   ├── page.tsx             # Models overview
│   │   │   └── [id]/
│   │   │       └── page.tsx         # Individual model page
│   │   ├── videos/
│   │   │   └── [id]/
│   │   │       └── page.tsx         # Video detail + predictions
│   │   ├── api/
│   │   │   ├── predict/
│   │   │   │   └── route.ts         # Proxy to HF Space
│   │   │   └── videos/
│   │   │       └── route.ts         # Fetch from Neon
│   │   └── dashboard/
│   │       └── page.tsx             # Embed Streamlit iframe
│   ├── components/
│   │   ├── ui/                      # Shadcn components
│   │   ├── VideoCard.tsx
│   │   ├── ModelCard.tsx
│   │   ├── PredictionWidget.tsx
│   │   ├── StatsDisplay.tsx
│   │   └── Navigation.tsx
│   ├── lib/
│   │   ├── api.ts                   # API client for HF Space
│   │   ├── db.ts                    # Neon connection
│   │   └── utils.ts
│   ├── public/
│   ├── styles/
│   ├── next.config.js
│   ├── package.json
│   ├── tsconfig.json
│   └── tailwind.config.js
│
├── training/                         # Model training scripts
│   ├── pipelines/
│   │   ├── velocity_pipeline.py
│   │   ├── clickbait_pipeline.py
│   │   ├── genre_pipeline.py
│   │   ├── tags_pipeline.py
│   │   ├── viral_pipeline.py
│   │   └── anomaly_pipeline.py
│   ├── feature_engineering/
│   │   ├── base_features.py
│   │   ├── temporal_features.py
│   │   └── text_features.py
│   ├── evaluation/
│   │   ├── metrics.py
│   │   └── validators.py
│   ├── utils/
│   │   ├── data_loader.py
│   │   └── model_uploader.py
│   └── config/
│       ├── training_config.yaml
│       └── model_registry.yaml
│
├── data-collection/                  # YouTube data pipeline
│   ├── collectors/
│   │   ├── youtube_client.py
│   │   ├── trending_scraper.py
│   │   └── channel_scraper.py
│   ├── processors/
│   │   ├── data_cleaner.py
│   │   └── deduplicator.py
│   ├── database/
│   │   ├── schema.sql
│   │   ├── models.py                # SQLAlchemy models
│   │   └── migrations/
│   └── config/
│       └── api_keys.yaml.example
│
├── .github/
│   └── workflows/
│       ├── data-pipeline.yml
│       ├── train-velocity.yml
│       ├── train-clickbait.yml
│       ├── train-genre.yml
│       ├── train-tags.yml
│       ├── train-viral.yml
│       ├── train-anomaly.yml
│       ├── deploy-hf-space.yml
│       ├── deploy-vercel.yml
│       ├── monitor-drift.yml
│       └── run-tests.yml
│
├── docs/
│   ├── API.md
│   ├── MODELS.md
│   ├── DEPLOYMENT.md
│   └── ARCHITECTURE.md
│
├── scripts/
│   ├── setup_hf_space.sh
│   ├── backup_models.py
│   ├── test_predictions.py
│   └── generate_dataset.py
│
├── .env.example
├── .gitignore
├── README.md
├── LICENSE
└── requirements-dev.txt