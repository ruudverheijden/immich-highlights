# Immich Photo Highlight Scoring Service (MVP)

This repository contains an MVP background service that searches an Immich
instance for time-based candidate photos, scores them, and creates highlight
albums via the Immich API.

## Quick start (local)

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

3. Optional: customize generated albums:

```bash
cp albums.toml.example albums.toml
# Edit albums.toml with the highlight albums you want
```

4. Run once:

```bash
python src/scorer.py
```

5. Docker (build & run):

```bash
docker compose up --build -d
```

## Development review export

```bash
python src/export_review.py
```

This writes `review/index.html` from the local SQLite database. It is a
development-only report for comparing scores with your own judgement and is not
used by the Docker service. Labels you enter in the report are stored only in
your browser's local storage. The export downloads thumbnails to
`review/thumbnails/` using `IMMICH_API_KEY`, so the report does not depend on
browser authentication or remote thumbnail URLs. Use `--no-download-thumbnails`
if you prefer direct Immich thumbnail URLs.

## Generated albums

The scorer uses Immich metadata search to build candidate sets before scoring.
Generated albums are configured in `albums.toml`:

```toml
[[albums]]
name = "Highlights: Last Week"
bucket = "last-week"
window_days = 7
limit = 15
max_candidates = 100
enabled = true
```

Each `[[albums]]` entry creates one rolling time-window album:

- `name`: the Immich album name
- `bucket`: stable internal id used to update the same generated album later
- `window_days`: how far back Immich should search by taken date
- `limit`: maximum number of top-scoring photos to put in the album
- `max_candidates`: maximum number of Immich search results to score for this album
- `enabled`: set to `false` to keep the config entry but skip the album

The default config creates:

- `Highlights: Last Week` for photos taken in the last 7 days
- `Highlights: Last Month` for photos taken in the last 30 days
- `Highlights: Last Year` for photos taken in the last 365 days

Each album rule asks Immich for image assets in its date range, scores only those
candidates, stores the score details in SQLite, and then syncs the generated
album to the current top results. If an asset checksum is already present in the
database, the stored score is reused instead of downloading and analyzing the
preview again.

For Docker, the image includes the default `/app/albums.toml`. To customize it,
copy `albums.toml.example` to `albums.toml` and mount it over that path:

```yaml
volumes:
  - ./albums.toml:/app/albums.toml:ro
```

# TODO
- Extend scoring, deduplication, and scheduling in follow-up iterations.


# Environment variables

Configure the service by copying `.env.example` to `.env` and editing the values.

- `IMMICH_API_URL`
  The Immich base URL as opened in your browser, without `/api`. The app adds
  `/api` internally for HTTP calls and uses this base URL for clickable log
  links. Example: `http://10.1.2.3:2283`.

- `IMMICH_API_KEY`
  A dedicated Immich API key. Create one in Immich with at least the permissions
  listed below, including `albumAsset.create` and `albumAsset.delete` for
  updating existing generated albums. Add `face.read` when you want the scorer
  to use Immich's own face detections for face-based scoring. Keep this secret
  and do not commit your `.env` file.

- `SCORER_DRY_RUN`
  Controls whether the service writes albums to Immich. Use `true` while testing;
  set to `false` when you want real albums to be created. Must be exactly
  `true` or `false`. Default: `true`.

- `SCORER_DB_PATH`
  Path to the SQLite database used to remember processed assets, scores, EXIF
  data, ratings, and detailed scoring JSON for later inspection or tuning. Must
  not be empty. Default: `./db/scorer.db`.

- `SCORER_SCAN_INTERVAL_HOURS`
  Intended interval for scheduled/background runs. The current one-shot command
  does not use scheduling yet, but Docker/background wiring can use this value.
  Must be a positive integer. Default: `24`.

- `SCORER_MAX_ASSETS`
  Maximum number of candidate assets to process per generated album rule. This
  protects large libraries from long scans and makes test runs predictable. Must
  be a positive integer up to `1000`. Default: `100`.

- `SCORER_BUCKET`
  Legacy label from the original single-album flow. The current time-based
  generator creates `Highlights: Last Week`, `Highlights: Last Month`, and
  `Highlights: Last Year` with fixed internal buckets. Must not be empty.
  Default: `MVP`.

- `SCORER_TEMP_DIR`
  Directory for temporary preview images downloaded from Immich while scoring.
  Files are removed after each asset is processed. Must not be empty. Default:
  `/tmp/scorer`.

- `SCORER_LOG_LEVEL`
  Python logging level, such as `INFO`, `DEBUG`, `WARNING`, or `ERROR`. Use
  `INFO` for normal runs and `DEBUG` only when you add debug logs. Must be one
  of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Default: `INFO`.

- `SCORER_ALBUM_CONFIG_PATH`
  Path to the TOML file that defines generated time-based albums. If the file is
  missing, the built-in default albums are used. Default: `./albums.toml` for
  local runs; the Docker image sets this to `/app/albums.toml`.

# Required API key permissions

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
- `face.read` (recommended — use Immich's own face detections for face-based scoring)
- `memory.read` (optional)
- `person.read` (optional)
- `person.statistics` (optional)
- `tag.create`, `tag.read`, `tag.update` (optional — for tag-based features)
- `user.read` (optional)

The scorer performs a lightweight permission check at startup for API calls it
actually uses. If you only want to test without writes, set `SCORER_DRY_RUN=true`
in `.env`.
