# AGENTS.md

Guidance for coding agents and contributors working on this repository.

## Project Purpose

This project is an Immich photo curation service. It searches Immich for photo
candidates, analyzes them locally, scores them, and creates or updates generated
highlight albums in Immich.

The default user experience is Docker-first. Local Python commands are mainly
for development and tuning.

## Entry Points

- The application entrypoint is `src/app.py`.
- Do not reintroduce `src/scorer.py`; scoring logic belongs in
  `src/scoring_engine.py`, orchestration belongs in `src/pipeline.py`, and the
  CLI belongs in `src/app.py`.
- The Docker image runs `python app.py` from `/app/src`.
- CLI argument parsing must reject unknown or abbreviated long options. The
  parser intentionally uses `allow_abbrev=False`, so `--force` must not be
  accepted as shorthand for `--force-rescore`.

## Architecture Direction

The codebase is moving toward a modular photo curation pipeline. Keep this
separation of responsibilities:

1. Asset discovery: synchronize metadata from Immich.
2. Filtering: exclude assets that should never be considered.
3. Technical analysis: compute objective image facts.
4. Semantic analysis: collect meaningful metadata and labels.
5. Event detection: group assets by time/location later.
6. Duplicate detection: detect exact and near-duplicate images later.
7. Scoring: convert facts into explainable score components.
8. Diversity selection: choose a good collection, not just highest scores.
9. Album generation: create or update Immich albums.

Current state: `src/pipeline.py` provides the pipeline spine, but
`src/album_generator.py` still contains several combined responsibilities. Keep
future changes moving responsibilities out of `album_generator.py` and into
independent stages instead of adding more behavior there by default.

Important design rule:

- Analysis stages produce facts.
- Scoring turns facts into explainable score components.
- Selection decides which scored assets should be included in albums.
- Album generation only syncs the selected assets to Immich.

## Database Design

The database has a legacy compatibility cache plus stage-oriented tables.

Keep `processed_assets` for the current cache and review export flow until the
pipeline is fully migrated.

Stage tables:

- `assets`: Immich-sourced metadata from discovery.
- `technical_analysis`: objective image facts such as blur, brightness,
  contrast, perceptual hash, and portrait quality.
- `semantic_analysis`: user/semantic facts such as rating, faces, location,
  favorites, edited status, and content-filter labels.
- `asset_scores`: scoring outputs only.
- `duplicate_groups` and `duplicate_group_members`: reserved for duplicate
  detection.

Do not put scoring inputs back into `asset_scores`. Inputs belong in
`technical_analysis` or `semantic_analysis`. `asset_scores` should contain only
score outputs such as `score`, `raw_score`, and `components_json`.

There are no migration scripts yet. The project currently accepts resetting the
local database during development.

## Incremental Processing

The service should avoid expensive reprocessing whenever possible.

Normal runs still query Immich candidates and fetch metadata, but unchanged
assets should not be downloaded and analyzed again. Cached scoring inputs may be
used to cheaply recalculate final scores after scoring config changes.

`--force-rescore` intentionally bypasses cached asset scores for current
candidates and re-downloads/re-analyzes previews. It must keep album mappings so
existing generated Immich albums are updated rather than forgotten.

## Immich API Rules

- Users configure `IMMICH_API_URL` as the browser/base URL without `/api`.
- `ImmichClient` appends `/api` internally for API calls.
- Search payloads should explicitly restrict to image timeline assets:
  - `type = "IMAGE"`
  - `visibility = "timeline"`
  - `withDeleted = False`
- Do not add later video filtering as a second line of defense unless the API
  filter is no longer sufficient.
- Content filters use Immich smart search and must be scoped through the same
  date-window logic as album candidates.

## Configuration Files

The Docker image includes default config files:

- `/app/albums.toml`
- `/app/content_filters.toml`
- `/app/scoring.toml`

Users can copy and mount local overrides. Keep end-user config simple.

Boolean environment variables must be exactly `true` or `false`. Do not accept
`1`, `0`, `yes`, or `no`.

Scoring variables live in `scoring.toml`. Comments in `scoring.toml.example`
should remain human-readable and explain what changing each value does.

The scoring config is not stored in the database. If users change it, rerunning
the service should be enough.

## Content Filters

Content filters are semantic labels first and scoring penalties second.

Immich smart search returns ranked matches but no absolute confidence score, so
filters use a minimum search pool. If a time window has too few photos, the
service widens the context and only applies matches that are also present in the
original album candidates.

If a photo matches multiple content filters, penalties must not stack. Keep all
labels for review, but apply only the penalty from the filter where Immich
ranked the photo highest, capped by `content_filter_min_penalty`.

## Scoring Rules

Keep `src/scoring_engine.py` focused on scoring. It should not perform image
analysis, API calls, database writes, album selection, or deduplication.

Scoring helpers should stay small, explainable, and individually testable.

The score details JSON in `processed_assets` currently remains for the legacy
cache/review flow. Avoid duplicating EXIF in score details; raw EXIF belongs in
the separate EXIF/database field.

## Deduplication Direction

Visual deduplication should be implemented as a post-scoring selection stage,
not as part of the score itself.

Use perceptual hashes (`phash`) stored in `technical_analysis`. Keep the
highest-scoring representative from visually similar groups. The first version
should be simple and configurable, likely using a Hamming-distance threshold.

## Review Export

`src/export_review.py` is a development/tuning tool, not part of the Docker
service flow. Keep it separated from normal service execution.

The review UI should help validate scoring decisions by showing thumbnails,
score components, scoring inputs, content-filter labels, and face overlays.

## Documentation Style

README order should stay end-user first:

1. Docker quick start.
2. What the service creates.
3. Permissions and basic configuration.
4. Advanced config overrides.
5. Developer/local usage near the end.
6. Architecture notes under developer notes.

Avoid moving developer architecture details back into the first-time Docker
setup path.

## Testing Expectations

Run these before finishing code changes:

```bash
.venv/bin/black .
.venv/bin/flake8 .
.venv/bin/python -m pytest
```

Use `.venv/bin/python -m pytest` rather than `.venv/bin/pytest`; the latter may
not put the repository root on the import path in this environment.

Add or update tests for every behavior change, especially:

- CLI flags and rejected parameters.
- Immich API request payloads.
- Database schema/write behavior.
- Scoring helper behavior.
- Pipeline orchestration boundaries.

## Worktree Notes

Do not touch `.DS_Store`; it may appear modified locally and is unrelated to
application changes.
