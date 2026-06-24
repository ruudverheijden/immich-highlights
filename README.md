# Immich Photo Highlight Scoring Service (MVP)

This repository contains an MVP background service that scans an Immich instance, scores photos, and creates highlight albums via the Immich API.

Quick start (local):

1. Create a virtualenv and install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure environment variables (example):

```bash
export IMMICH_API_URL=http://immich:2283
export IMMICH_API_KEY=your_api_key
export SCORER_DB_PATH=./db/scorer.db
export SCORER_DRY_RUN=true
```

3. Run once:

```bash
python src/scorer.py
```

Docker (build & run):

```bash
docker compose up --build -d
```

Notes:
- MVP implements blur, face detection, basic EXIF signals, and a simple album creation flow (dry-run by default).
- Extend scoring, deduplication, and scheduling in follow-up iterations.
