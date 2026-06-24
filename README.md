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

Environment variables
---------------------

Configure the service by copying `.env.example` to `.env` and editing the values.

- `IMMICH_API_URL`
  The Immich base URL as opened in your browser, without `/api`. The app adds
  `/api` internally for HTTP calls and uses this base URL for clickable log
  links. Example: `http://10.1.2.3:2283`.

- `IMMICH_API_KEY`
  A dedicated Immich API key. Create one in Immich with at least the permissions
  listed below. Keep this secret and do not commit your `.env` file.

- `SCORER_DRY_RUN`
  Controls whether the service writes albums to Immich. Use `true` while testing;
  set to `false` when you want real albums to be created. Must be exactly
  `true` or `false`. Default: `true`.

- `SCORER_DB_PATH`
  Path to the SQLite database used to remember processed assets, scores, EXIF
  data, face counts, blur values, and ratings. Must not be empty. Default:
  `./db/scorer.db`.

- `SCORER_SCAN_INTERVAL_HOURS`
  Intended interval for scheduled/background runs. The current one-shot command
  does not use scheduling yet, but Docker/background wiring can use this value.
  Must be a positive integer. Default: `24`.

- `SCORER_MAX_ASSETS`
  Maximum number of assets to process in one run. This protects large libraries
  from long scans and makes test runs predictable. Must be a positive integer
  up to `1000`. Default: `100`.

- `SCORER_BUCKET`
  Label used in generated album names. For example, `SCORER_BUCKET=Summer`
  creates albums named `Highlights: Summer`. Must not be empty. Default: `MVP`.

- `SCORER_TEMP_DIR`
  Directory for temporary preview images downloaded from Immich while scoring.
  Files are removed after each asset is processed. Must not be empty. Default:
  `/tmp/scorer`.

- `SCORER_LOG_LEVEL`
  Python logging level, such as `INFO`, `DEBUG`, `WARNING`, or `ERROR`. Use
  `INFO` for normal runs and `DEBUG` only when you add debug logs. Must be one
  of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Default: `INFO`.

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
