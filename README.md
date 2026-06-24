# Immich Photo Highlight Scoring Service (MVP)

This repository contains an MVP background service that scans an Immich instance, scores photos, and creates highlight albums via the Immich API.

Quick start (local):

1. Create a virtualenv and install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy and configure `.env`:

```bash
cp .env.example .env
# Edit .env with your Immich API URL, key, and other settings
```

`IMMICH_API_URL` should be the Immich base URL as opened in your browser,
without `/api`. For example, use `http://10.1.2.3:2283`.

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

Required API permissions
------------------------

Create a dedicated Immich API key with the following minimal permissions for the scorer to operate properly:

- `asset.read` (list and download assets)
- `asset.update` (optional — modify asset metadata if you implement writes)
- `asset.view` (view asset metadata)
- `album.create` (create highlight albums)
- `album.read` (list/read albums)
- `album.update` (update album metadata)
- `album.statistics` (optional)
- `albumAsset.create` (add assets to an album)
- `albumAsset.delete` (remove assets from an album)
- `archive.read` (optional)
- `face.read` (optional — for face-based features)
- `memory.read` (optional)
- `person.read` (optional)
- `person.statistics` (optional)
- `server.about` (lightweight connectivity check)
- `tag.create`, `tag.read`, `tag.update` (optional — for tag-based features)
- `user.read` (optional)

The scorer performs a lightweight permission check at startup (it will attempt harmless GET/OPTIONS requests). If you only want to test without writes, set `SCORER_DRY_RUN=true` in `.env`.
