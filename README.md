# Immich Photo Highlight Scoring Service (MVP)

This service creates rolling highlight albums in Immich. It searches your Immich
library for recent photos, scores the candidates, and creates or updates albums
such as `Highlights: Last Week`, `Highlights: Last Month`, and
`Highlights: Last Year`.

The default setup is meant to run as a Docker container. You only need an Immich
URL, an API key, and a small persistent database folder.

## Quick Start With Docker

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Edit `.env` and set at least:

```env
IMMICH_API_URL=http://your-immich-host:2283
IMMICH_API_KEY=your_immich_api_key_here
SCORER_DRY_RUN=true
```

Use the same Immich base URL you open in your browser. Do not add `/api`; the
service adds that internally.

3. Start the container:

```bash
docker compose up --build -d
```

4. Check the logs:

```bash
docker compose logs -f photo-scorer
```

Keep `SCORER_DRY_RUN=true` for the first run. The service will score photos and
log what it would do, but it will not create or update albums in Immich.

5. When the logs look good, set this in `.env`:

```env
SCORER_DRY_RUN=false
```

Then restart:

```bash
docker compose up -d
```

## What It Creates

By default the service creates these generated albums:

- `Highlights: Last Week` for photos taken in the last 7 days
- `Highlights: Last Month` for photos taken in the last 30 days
- `Highlights: Last Year` for photos taken in the last 365 days

Each run asks Immich for image assets in the configured date ranges, excludes
non-timeline and deleted assets, scores the candidates, stores score details in
SQLite, and syncs each generated album to the current top results.

The generated album mapping is stored locally, so reruns update the same Immich
albums instead of creating new albums every time.

## API Key Permissions

Create a dedicated Immich API key for this service. The key needs these
permissions for the default Docker setup:

- `asset.read` to list metadata and download image previews
- `asset.statistics` to count photos for content-filter search windows
- `asset.view` to read asset metadata
- `album.create` to create generated highlight albums
- `album.read` to read existing generated albums
- `album.update` to update album metadata
- `albumAsset.create` to add assets to generated albums
- `albumAsset.delete` to remove assets from generated albums on reruns
- `face.read` to use Immich's own face detections for face-based scoring

Optional permissions that are currently not required for the default flow:

- `album.statistics`
- `archive.read`
- `memory.read`
- `person.read`
- `person.statistics`
- `tag.create`, `tag.read`, `tag.update`
- `user.read`
- `asset.update`

The service performs a lightweight permission check at startup and logs failed
checks. If you only want to test without writes, keep `SCORER_DRY_RUN=true`.

## Basic Configuration

Most users only need `.env`.

- `IMMICH_API_URL`
  The Immich base URL as opened in your browser, without `/api`. Example:
  `http://10.1.2.3:2283`.

- `IMMICH_API_KEY`
  A dedicated Immich API key. Keep this secret and do not commit `.env`.

- `SCORER_DRY_RUN`
  Controls whether the service writes albums to Immich. Use `true` while
  testing; set to `false` when you want real albums to be created. Must be
  exactly `true` or `false`. Default: `true`.

- `SCORER_DB_PATH`
  Path to the SQLite database. In Docker this should usually stay
  `/app/db/scorer.db`, with `./db` mounted as a volume. Default:
  `./db/scorer.db`.

- `SCORER_LOG_LEVEL`
  Python logging level, such as `INFO`, `DEBUG`, `WARNING`, or `ERROR`.
  Default: `INFO`.

- `SCORER_MAX_ASSETS`
  Maximum number of candidate assets to process per generated album rule. This
  protects large libraries from long scans and makes test runs predictable. Must
  be a positive integer up to `1000`. Default: `100`.

## Persistent Data

Docker Compose mounts these folders by default:

```yaml
volumes:
  - ./db:/app/db
  - ./log:/app/log
  - /tmp/scorer:/tmp/scorer
```

The important one is `./db`. It contains the SQLite database with processed
asset checksums, scores, detailed scoring inputs, and generated album mappings.
Keep this folder if you want reruns to update existing generated albums and
avoid reprocessing unchanged photos.

## Incremental Scanning

The service is incremental for the expensive image-analysis work. Every run
still asks Immich for the current album candidates and fetches metadata for each
candidate, because it needs to know which photos are in the configured time
windows and whether their checksums changed.

Unchanged photos are not downloaded and analyzed again. If a candidate asset has
the same checksum and the same content-filter state as the previous run, the
service reuses the normalized scoring inputs stored in SQLite. It can then
recalculate the final score cheaply, for example after changing `scoring.toml`,
without recomputing blur, brightness, contrast, face quality, or portrait
quality from the image preview.

An image preview is downloaded and analyzed again only when the asset is new,
the checksum changed, the content-filter labels changed, or the old database row
does not contain enough scoring inputs to recalculate from cache.

To force a full rescan of the current candidates, run the scorer with
`--force-rescore`. This ignores cached asset scores and re-downloads/re-analyzes
the previews for photos that are currently in the configured album windows. It
keeps the local generated album mappings, so existing Immich highlight albums
are updated instead of forgotten.

With Docker Compose:

```bash
docker compose run --rm photo-scorer python scorer.py --force-rescore
```

For local development:

```bash
python src/scorer.py --force-rescore
```

## Advanced Configuration Files

The Docker image includes default config files inside the container:

- `/app/albums.toml`
- `/app/content_filters.toml`
- `/app/scoring.toml`

You can run the service without creating any of these files yourself. To
override one, copy the matching example file and mount it into the container.

```bash
cp albums.toml.example albums.toml
cp content_filters.toml.example content_filters.toml
cp scoring.toml.example scoring.toml
```

Then uncomment the matching mount in `docker-compose.yml`:

```yaml
volumes:
  - ./albums.toml:/app/albums.toml:ro
  - ./content_filters.toml:/app/content_filters.toml:ro
  - ./scoring.toml:/app/scoring.toml:ro
```

You can override the paths with environment variables:

- `SCORER_ALBUM_CONFIG_PATH`
  Path to the TOML file that defines generated time-based albums. If the file is
  missing, the built-in default albums are used. Docker default:
  `/app/albums.toml`.

- `SCORER_CONTENT_FILTER_CONFIG_PATH`
  Path to the TOML file that defines optional smart-search content filters. If
  the file is missing, the built-in default filters are used. Docker default:
  `/app/content_filters.toml`.

- `SCORER_SCORING_CONFIG_PATH`
  Path to the TOML file that defines scoring weights and thresholds. If the file
  is missing, the built-in defaults are used. Docker default:
  `/app/scoring.toml`.

Other advanced environment variables:

- `SCORER_SCAN_INTERVAL_HOURS`
  Intended interval for scheduled/background runs. The current command runs one
  scoring pass when the container starts, but this value is kept for future
  scheduling/background wiring. Must be a positive integer. Default: `24`.

- `SCORER_TEMP_DIR`
  Directory for temporary preview images downloaded from Immich while scoring.
  Files are removed after each asset is processed. Docker Compose mounts
  `/tmp/scorer` by default. Must not be empty. Default: `/tmp/scorer`.

- `SCORER_BUCKET`
  Legacy label from the original single-album flow. The current generated album
  flow uses each album's `bucket` from `albums.toml`. Must not be empty.
  Default: `MVP`.

## Custom Albums

Generated albums are configured in `albums.toml`. This is the main file most
users customize:

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
- `max_candidates`: maximum number of Immich search results to score for this
  album
- `enabled`: set to `false` to keep the config entry but skip the album

## Content Filters

Content filters are configured in `content_filters.toml`. Most users can keep
the default file unchanged.

Each `[[content_filters]]` entry runs an Immich smart search to find content
that should be penalized, such as screenshots, receipts, documents, or photos of
screens. Assets found by those searches get labels in `score_details_json` and
receive the configured score penalty.

- `label`: label stored in scoring details, such as `screenshot`
- `query`: Immich smart-search query to run
- `penalty`: score adjustment for matching assets, usually negative
- `max_results`: maximum ranked smart-search results to fetch per filter run.
  Immich does not expose a similarity score here, so lower values keep labels
  limited to the strongest results.
- `min_search_pool`: minimum number of photos Immich should be able to compare
  before the filter is trusted. If the album window is smaller than this, the
  service automatically widens the search context and then only applies matches
  that are also in the original album candidates.
- `enabled`: set to `false` to keep the config entry but skip the filter

To disable content filters entirely, create `content_filters.toml` with this
top-level setting:

```toml
content_filters = []
```

Content filters are intentionally more careful than a direct Immich smart
search. Immich returns ranked matches, but it does not expose an absolute
confidence score. If a filter searches a small album window, Immich can still
return the "best" results even when none are truly good matches.

To reduce false positives, the service uses this flow:

1. Build the normal album candidate list from the album's `window_days`.
2. For each content filter, start smart search with the same date window.
3. If that window has fewer than `min_search_pool` photos, automatically widen
   the search context until there are enough photos to compare.
4. Run Immich smart search in that wider context.
5. Apply the penalty only to smart-search results that also appear in the
   original album candidates.

A photo may match multiple content filters. All labels are stored for review,
but the score uses only the penalty from the filter where Immich ranked that
photo highest. This avoids stacking several penalties for one similar-looking
photo.

Smart-search queries are semantic, not strict keyword filters. A query with
multiple words is treated as one natural-language phrase. It is not an `AND`
search where every word must be present, and it is not a plain `OR` search
where any single word is enough. Immich ranks photos by how similar they are to
the whole query.

For predictable filters, keep queries short and specific. Prefer separate
filters when you want separate concepts:

```toml
[[content_filters]]
label = "computer-screen"
query = "computer screen"
penalty = -20
max_results = 15
min_search_pool = 500
enabled = true

[[content_filters]]
label = "phone-screen"
query = "phone screen"
penalty = -20
max_results = 15
min_search_pool = 500
enabled = true
```

Use `max_results` as the strictness knob. A high value fetches deeper ranked
results and can label weak matches; a low value only labels the strongest
matches. Use `min_search_pool` as the reliability knob. Start with
`max_results = 10` to `25` and `min_search_pool = 500`, export the review HTML,
and tune from there.

## Scoring Config

Scoring weights and thresholds are configured in `scoring.toml`. Start with
`scoring.toml.example`, run the service, inspect the review export, and adjust
values until the ranking matches your own taste.

The scoring config is intentionally not stored in the database. Cached photos
store normalized scoring inputs, so rerunning the service can recalculate scores
with the latest config without downloading every unchanged image again.

The file has three sections:

- `[weights]`: general bonuses such as favorites, ratings, faces, location, and
  portrait-like photos
- `[technical_quality]`: blur, resolution, ISO, exposure, contrast, and
  brightness thresholds
- `[content_filters]`: the minimum content-filter penalty cap

Example:

```toml
[weights]
base_score = 50
favorite_bonus = 20
rating_step = 15

[technical_quality]
blur_low_threshold = 50
blur_low_penalty = -20
blur_high_threshold = 200
blur_high_bonus = 10

[content_filters]
content_filter_min_penalty = -50
```

Only numeric values are accepted. Unknown field names fail fast at startup so
typos do not silently change scoring behavior.

## Review Export

The review export is a development/tuning tool. It writes `review/index.html`
from the local SQLite database so you can compare calculated scores with your
own judgement.

Run it locally:

```bash
python src/export_review.py
```

Labels you enter in the report are stored only in your browser's local storage.
The export downloads thumbnails to `review/thumbnails/` using `IMMICH_API_KEY`,
so the report does not depend on browser authentication or remote thumbnail
URLs. Use `--no-download-thumbnails` if you prefer direct Immich thumbnail URLs.

## Local Development

Docker is the recommended way to run the service as an end user. For local
development, use a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Copy and configure `.env`:

```bash
cp .env.example .env
```

Optional local overrides:

```bash
cp albums.toml.example albums.toml
cp content_filters.toml.example content_filters.toml
cp scoring.toml.example scoring.toml
```

Run once:

```bash
python src/scorer.py
```

Run checks:

```bash
.venv/bin/black .
.venv/bin/flake8 .
.venv/bin/python -m pytest
```

## Developer Notes

Future functionality ideas:

- Fine tune scoring
- Deduplicate based on visual similarity
- Deduplicate based on location and time
- Test the service running in Docker on Synology
- Test larger photo libraries
- Add a maintenance command to clear the local database and start fresh
