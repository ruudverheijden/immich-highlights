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

Optional: customize content filters:

```bash
cp content_filters.toml.example content_filters.toml
# Edit content_filters.toml only when you want different smart-search filters
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
Generated albums are configured in `albums.toml`. This is the main file most
users will customize:

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

Content filters are configured separately in `content_filters.toml`. Most users
can keep the default file unchanged. Each `[[content_filters]]` entry runs an
Immich smart search to find content that should be penalized. Assets found by
those searches get labels in `score_details_json` and receive the configured
score penalty:

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

Content filters are intentionally a little more careful than a direct Immich
smart search. Immich returns ranked matches, but it does not expose an absolute
confidence score. If a filter searches a small album window, Immich can still
return the "best" results even when none are truly good matches. That creates
false positives.

To reduce that, the service uses this flow:

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

For example, a `Last Week` album may contain only 80 photos. A direct smart
search for `computer screen` inside those 80 photos can produce weak matches.
With `min_search_pool = 500`, the service may widen the context to several
months, ask Immich for the strongest screen-like photos in that larger set, and
then penalize only the matching photos from the original week.

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
matches. Use `min_search_pool` as the reliability knob. A higher value makes the
service widen the context more before trusting smart search. Start with
`max_results = 10` to `25` and `min_search_pool = 500`, export the review HTML,
and tune from there.

The default content filter config penalizes likely screenshots,
documents/receipts, and display-like photos. If `content_filters.toml` is
missing, the built-in default filters are used. To disable content filters
entirely, create `content_filters.toml` with this top-level setting:

```toml
content_filters = []
```

The default config creates:

- `Highlights: Last Week` for photos taken in the last 7 days
- `Highlights: Last Month` for photos taken in the last 30 days
- `Highlights: Last Year` for photos taken in the last 365 days

Each album rule asks Immich for image assets in its date range, scores only those
candidates, stores the score details in SQLite, and then syncs the generated
album to the current top results. If an asset checksum is already present in the
database, the stored score is reused instead of downloading and analyzing the
preview again.

For Docker, the image includes default `/app/albums.toml` and
`/app/content_filters.toml` files. To customize albums, copy
`albums.toml.example` to `albums.toml` and mount it over that path. To customize
content filters, copy `content_filters.toml.example` to `content_filters.toml`
and mount it too:

```yaml
volumes:
  - ./albums.toml:/app/albums.toml:ro
  - ./content_filters.toml:/app/content_filters.toml:ro
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

- `SCORER_CONTENT_FILTER_CONFIG_PATH`
  Path to the TOML file that defines optional smart-search content filters. If
  the file is missing, the built-in default filters are used. Default:
  `./content_filters.toml` for local runs; the Docker image sets this to
  `/app/content_filters.toml`.

# Required API key permissions

Create a dedicated Immich API key with the following minimal permissions for the scorer to operate properly:

- `asset.read` (list and download assets)
- `asset.statistics` (count assets in content-filter search windows)
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

# TODO
Future functionalities to include:
- Fine tune scoring
- Deduplicate based on similarity of photos
- Deduplicate based on location: only take the highest scoring photos if multiple photos are taken at roughly the same place and time
- Test to service running in Docker on Synology
- Test for large photo libraries
- Add feature to force cleaning the database and start from scratch
