# AGENTS.md

Guidance for coding agents and contributors working on this repository.

This is an Immich photo curation service. It searches Immich, analyzes local
previews, scores photo candidates, selects diverse highlights, and creates or
updates generated albums. The default user experience is Docker-first; local
Python commands are mainly for development and tuning.

## Entry Points

- App entrypoint: `src/app.py`
- Docker runs `python app.py` from `/app/src`
- Do not reintroduce `src/scorer.py`
- Scoring logic belongs in `src/scoring_engine.py`
- Orchestration belongs in `src/pipeline.py`
- CLI parsing belongs in `src/app.py`

## Architecture Boundaries

Keep the pipeline modular:

1. Asset discovery
2. Filtering
3. Technical analysis
4. Semantic analysis
5. Event detection
6. Duplicate detection
7. Scoring
8. Diversity selection
9. Album generation

Rules:

- Analysis stages produce facts.
- Scoring turns facts into explainable score components.
- Selection chooses assets for albums.
- Album generation only syncs selected asset IDs to Immich.
- Move reusable behavior out of coordinators and into stage modules.

Current modules:

- `src/pipeline.py`: pipeline spine
- `src/curation.py`: per-album candidate selection
- `src/album_generator.py`: final Immich album sync
- `src/asset_discovery.py`: Immich metadata synchronization
- `src/filtering.py`: candidate exclusion decisions and reasons
- `src/technical_analysis.py`: objective image facts
- `src/semantic_analysis.py`: user, location, face, and content facts
- `src/duplicate_detection.py`: post-scoring visual duplicate groups
- `src/selection.py`: diversity and final album asset selection

## Database Rules

Keep `processed_assets` for the legacy cache/review export until migration is
complete.

Stage tables:

- `assets`: Immich metadata
- `asset_filter_results`: per-album filtering decisions
- `technical_analysis`: blur, brightness, contrast, pHash, portrait quality
- `semantic_analysis`: ratings, faces, location, favorites, edits, content labels
- `asset_scores`: score outputs only
- `duplicate_groups`, `duplicate_group_members`: post-scoring duplicate groups

Do not put scoring inputs in `asset_scores`. Inputs belong in
`technical_analysis` or `semantic_analysis`. `asset_scores` should contain only
score outputs such as `score`, `raw_score`, and `components_json`.

No migrations exist yet; local database resets are acceptable during
development.

## Processing Rules

Normal runs should reuse cached analysis for unchanged assets.

`--force-rescore` must:

- bypass cached scores for current candidates
- re-download and re-analyze previews
- preserve album mappings so existing generated albums are updated

## Immich API Rules

- `IMMICH_API_URL` is configured without `/api`
- `ImmichClient` appends `/api`
- Search payloads must restrict to:
  - `type = "IMAGE"`
  - `visibility = "timeline"`
  - `withDeleted = False`
- Do not add later video filtering unless the API filter is no longer sufficient.
- Content filters use Immich smart search and must respect album date windows.

## Config Rules

Docker includes:

- `/app/albums.toml`
- `/app/content_filters.toml`
- `/app/scoring.toml`

Rules:

- Boolean env vars must be exactly `true` or `false`.
- Scoring config lives in `scoring.toml`, not the database.
- Rerunning the service should apply scoring config changes.
- Keep `scoring.toml.example` comments human-readable and explanatory.

## Content Filters

Content filters are semantic labels first and scoring penalties second.

If a time window has too few photos, widen the search context, but only apply
matches that are also original album candidates.

If multiple filters match one photo:

- keep all labels for review
- apply only the penalty from the best-ranked matching filter
- cap by `content_filter_min_penalty`

## Scoring Rules

Keep `src/scoring_engine.py` limited to scoring. It must not perform image
analysis, API calls, database writes, album selection, or deduplication.

Scoring helpers should stay small, explainable, and individually testable.

Score details may remain in `processed_assets` for legacy cache/review flow. Do
not duplicate raw EXIF in score details.

## Deduplication Rules

Deduplication is post-scoring selection, not scoring.

Use pHash from `technical_analysis`. Keep the highest-scoring representative
from visually similar groups.

Timestamp duplicate detection may confirm bursts, but must never group on
timestamp alone. It also requires photos to be within the configured seconds
window and pHash threshold.

## Review Export

`src/export_review.py` is a development/tuning tool only. Keep it out of normal
service flow.

## README Style

Keep README end-user first:

1. Docker quick start
2. What the service creates
3. Permissions and basic config
4. Advanced config overrides
5. Developer/local usage
6. Architecture notes

## Testing

Before finishing code changes, run:

```bash
.venv/bin/black .
.venv/bin/flake8 .
.venv/bin/python -m pytest
```

Use `.venv/bin/python -m pytest`, not `.venv/bin/pytest`.

Add or update tests for behavior changes, especially CLI flags, Immich API
payloads, database writes, scoring helpers, and pipeline boundaries.