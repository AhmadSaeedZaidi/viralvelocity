# Pleiades: VLM + Knowledge Graph Project

Pleiades is a modular, microservices-based research platform engineered for large-scale, automated ingestion and analysis of diverse YouTube data—including video frames, channel metadata, text transcripts, engagement statistics, and much more. Architected for high-throughput collection and efficient storage, the system utilizes comprehensive automation and professional-grade DevOps practices throughout the entire pipeline.

More than just data ingestion, Pleiades is purpose-built for cutting-edge AI research workflows. It supports orchestrated training and fine-tuning of a wide range of models—including vision models, NLP, and VLMs—and enables construction of knowledge graphs (OWL ontologies), model mixing, and advanced analytics. Flexible APIs are served using platforms like Hugging Face, and model development and evaluation integrate seamlessly with scheduled Kaggle notebook jobs. This unified platform brings scalable infrastructure, automated pipelines, and robust model experimentation together to accelerate reproducible research in computer vision, natural language processing, and knowledge representation.

---

## System Architecture

Pleiades is structured as a monorepo separating core infrastructure from micro-services:

- **Atlas (Infrastructure Core):**  
  A unified kernel handling database connections (Neon/Postgres), object storage interfaces (GCS/S3), and system-wide configuration. It enforces the "Data Tiering" policy (Hot/Warm/Cold).

- **Maia (Ingestion Service):**  
  The primary collection engine, composed of four specialized modules:
  - **The Hunter:** Discovery of new content via topic-based search.
  - **The Tracker:** Longitudinal monitoring of engagement velocity.
  - **The Profiler:** Detection of channel rebranding and entity masquerading.
  - **The Sniper:** "Vectorize & Vanish" visual analysis using CLIP embeddings.

- **Electra (ML Pipeline):**  
  *(Planned)* Training of Temporal Fusion Transformers (TFT) for anomaly detection.

- **Alcyone (Testing Bench):**  
  A flexible environment for running experiments, integration tests, and rapid prototyping across the stack.

> For detailed schemas, guarantees, and SLAs, see [ARCHITECTURE.md](ARCHITECTURE.md)

---

## Key Technical Capabilities

**1. Vectorize & Vanish Protocol**  
To perform deep visual analysis without massive storage costs, Pleiades implements a streaming vectorization pipeline. Video frames are extracted in-memory, converted to 512-dimensional embeddings via ONNX-optimized CLIP models, and immediately discarded. Only the mathematical representation is stored.

**2. Distributed Resource Pooling**  
Pleiades implements a robust Token Bucket algorithm for API management. It supports high-availability deployments by pooling multiple API credentials to distribute load and prevent service interruption during quota exhaustion events, while maintaining strict global budget caps.

**3. Event Sourcing & Replayability**  
The system creates an immutable audit log of all discoveries. This allows the entire relational database to be wiped and reconstructed ("rehydrated") from the raw object storage logs, enabling safe iteration on parsing logic without data loss.

---

## Compliance & Data Governance

Pleiades is engineered strictly for aggregate content analysis and misinformation research. It includes a configurable `COMPLIANCE_MODE` to ensure adherence to platform Terms of Service.

- **Compliance Mode (Default: ON):**  
  Enforces strict 30-day data retention policies, automatically purging metadata and PII. Limits API usage to standard quotas.

- **No User Profiling:**  
  The system monitors public content creators (channels) and video metrics. It does not collect, track, or store data regarding individual viewers or subscribers.

- **Zero-Retention Policy:**  
  To respect copyright and privacy, no raw video files or image frames are retained on disk. All visual analysis is ephemeral.

---

## Technology Stack

- **Language:** Python 3.11 (Type Hinted)
- **Database:** Neon (Serverless Postgres + pgvector)
- **Storage:** Google Cloud Storage (JSON/Parquet)
- **Compute:** GitHub Actions (Matrix Strategy for parallel ingestion)
- **ML:** PyTorch, ONNX Runtime, Hugging Face Transformers
- **Validation:** Pydantic (Strict Schema Enforcement)

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.