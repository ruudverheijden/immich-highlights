"""Development-only HTML review export for inspecting scores and thumbnails."""

import argparse
from datetime import datetime
import html
import json
import os
import sqlite3
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


DEFAULT_DB_PATH = "./db/scorer.db"
DEFAULT_OUTPUT_PATH = "./review/index.html"
DEFAULT_IMMICH_URL = "http://localhost:2283"
PLACEHOLDER_THUMBNAIL = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "viewBox='0 0 400 300'%3E%3Crect width='400' height='300' "
    "fill='%23dfe4ec'/%3E%3Ctext x='200' y='150' text-anchor='middle' "
    "font-family='sans-serif' font-size='22' fill='%23657187'%3E"
    "No thumbnail%3C/text%3E%3C/svg%3E"
)


def immich_asset_url(immich_url: str, asset_id: str) -> str:
    """Build a browser URL for opening an asset in Immich."""
    return f"{immich_url.rstrip('/')}/photos/{asset_id}"


def immich_thumbnail_url(immich_url: str, asset_id: str) -> str:
    """Build a browser URL for loading an Immich preview thumbnail."""
    return f"{immich_url.rstrip('/')}/api/assets/{asset_id}/thumbnail?size=preview"


def thumbnail_filename(asset_id: str) -> str:
    """Return a stable local thumbnail filename for an asset."""
    return f"{asset_id}.jpg"


def download_thumbnail(
    session,
    immich_url: str,
    api_key: str,
    asset_id: str,
    thumbnail_dir: Path,
) -> Path | None:
    """Download one thumbnail locally so the report does not depend on browser auth."""
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    path = thumbnail_dir / thumbnail_filename(asset_id)
    if path.exists():
        return path

    headers = {"x-api-key": api_key} if api_key else {}
    try:
        response = session.get(
            immich_thumbnail_url(immich_url, asset_id),
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    path.write_bytes(response.content)
    return path


def attach_local_thumbnails(
    assets: list[dict],
    immich_url: str,
    api_key: str,
    output_path: str,
) -> int:
    """Attach local thumbnail paths to assets when downloads succeed."""
    output_dir = Path(output_path).parent
    thumbnail_dir = output_dir / "thumbnails"
    session = requests.Session()
    downloaded = 0
    for asset in assets:
        path = download_thumbnail(
            session,
            immich_url,
            api_key,
            asset["asset_id"],
            thumbnail_dir,
        )
        if path:
            asset["thumbnail_src"] = path.relative_to(output_dir).as_posix()
            downloaded += 1
        else:
            asset["thumbnail_src"] = PLACEHOLDER_THUMBNAIL
    return downloaded


def parse_json(value, fallback):
    """Parse a JSON string from SQLite without leaking decode errors to callers."""
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


PHOTO_DATETIME_KEYS = (
    "localDateTime",
    "dateTimeOriginal",
    "DateTimeOriginal",
    "dateTime",
    "DateTime",
    "fileCreatedAt",
    "createdAt",
)


def first_photo_datetime(exif: dict, fallback: str | None = None) -> str:
    """Return the best available photo datetime for the review card."""
    for key in PHOTO_DATETIME_KEYS:
        value = exif.get(key)
        if value:
            return format_datetime(value)
    return format_datetime(fallback) if fallback else "Unknown datetime"


def format_datetime(value) -> str:
    """Format common ISO datetime values while leaving EXIF strings readable."""
    text = str(value)
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %H:%M")


def load_processed_assets(db_path: str, limit: int | None = None) -> list[dict]:
    """Read scored assets from the database in descending score order."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    sql = (
        "SELECT asset_id, score, rating, score_details_json, processed_at, exif_json "
        "FROM processed_assets ORDER BY score DESC, processed_at DESC"
    )
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    assets = []
    for row in rows:
        exif = parse_json(row[5], {})
        assets.append(
            {
                "asset_id": row[0],
                "score": row[1],
                "rating": row[2],
                "score_details": parse_json(row[3], {}),
                "processed_at": row[4],
                "photo_datetime": first_photo_datetime(exif, fallback=row[4]),
            }
        )
    return assets


def load_album_memberships(db_path: str) -> tuple[list[dict], dict[str, list[dict]]]:
    """Read generated album mappings and index them by asset id."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT album_name, bucket, asset_ids_json "
        "FROM album_mappings ORDER BY album_name"
    )
    rows = cur.fetchall()
    conn.close()

    albums = []
    by_asset_id = {}
    for album_name, bucket, asset_ids_json in rows:
        asset_ids = parse_json(asset_ids_json, [])
        if not isinstance(asset_ids, list):
            asset_ids = []
        album = {
            "name": album_name or bucket,
            "bucket": bucket,
            "asset_ids": asset_ids,
        }
        albums.append(album)
        for asset_id in asset_ids:
            by_asset_id.setdefault(asset_id, []).append(
                {"name": album["name"], "bucket": bucket}
            )
    return albums, by_asset_id


def load_duplicate_memberships(db_path: str) -> dict[str, list[dict]]:
    """Read duplicate-group memberships and index them by asset id."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT g.album_bucket, g.representative_asset_id, g.reason, "
        "m.asset_id, m.distance "
        "FROM duplicate_groups g "
        "JOIN duplicate_group_members m ON g.group_id = m.group_id"
    )
    rows = cur.fetchall()
    conn.close()

    memberships = {}
    for bucket, representative_id, reason, asset_id, distance in rows:
        memberships.setdefault(asset_id, []).append(
            {
                "bucket": bucket,
                "role": (
                    "representative" if asset_id == representative_id else "suppressed"
                ),
                "reason": reason,
                "distance": distance,
                "representative_asset_id": representative_id,
            }
        )
    return memberships


def load_filter_memberships(db_path: str) -> dict[str, list[dict]]:
    """Read per-album filtering decisions and index them by asset id."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "SELECT asset_id, album_bucket, included, reason " "FROM asset_filter_results"
    )
    rows = cur.fetchall()
    conn.close()

    memberships = {}
    for asset_id, bucket, included, reason in rows:
        memberships.setdefault(asset_id, []).append(
            {
                "bucket": bucket,
                "included": bool(included),
                "reason": reason,
            }
        )
    return memberships


def query_single_value(cur, sql: str, params=(), default=0):
    """Return one scalar query value with a safe default."""
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row or row[0] is None:
        return default
    return row[0]


def placeholders(values: list[str]) -> str:
    """Return SQLite placeholders for a non-empty list."""
    return ",".join("?" for _value in values)


def unique_asset_ids_for_bucket(cur, bucket: str | None) -> list[str]:
    """Return candidate asset ids for one album bucket or all buckets."""
    if bucket is None:
        cur.execute("SELECT DISTINCT asset_id FROM asset_filter_results")
    else:
        cur.execute(
            "SELECT DISTINCT asset_id FROM asset_filter_results WHERE album_bucket = ?",
            (bucket,),
        )
    return [row[0] for row in cur.fetchall()]


def count_for_assets(
    cur, table_name: str, asset_ids: list[str], where: str = ""
) -> int:
    """Count rows in a stage table for a known asset set."""
    if not asset_ids:
        return 0
    sql = (
        f"SELECT COUNT(*) FROM {table_name} "
        f"WHERE asset_id IN ({placeholders(asset_ids)})"
    )
    if where:
        sql += f" AND {where}"
    return query_single_value(cur, sql, asset_ids)


def score_stats_for_assets(cur, asset_ids: list[str]) -> dict:
    """Return score count and range for a known asset set."""
    if not asset_ids:
        return {"count": 0, "average": 0, "highest": 0, "lowest": 0}
    cur.execute(
        "SELECT COUNT(*), AVG(score), MAX(score), MIN(score) "
        "FROM processed_assets "
        f"WHERE asset_id IN ({placeholders(asset_ids)})",
        asset_ids,
    )
    count, average, highest, lowest = cur.fetchone()
    return {
        "count": count or 0,
        "average": round(average or 0, 1),
        "highest": highest or 0,
        "lowest": lowest or 0,
    }


def content_label_counts(cur, asset_ids: list[str]) -> dict[str, int]:
    """Count content-filter labels from semantic analysis for a known asset set."""
    if not asset_ids:
        return {}
    cur.execute(
        "SELECT content_labels_json FROM semantic_analysis "
        f"WHERE asset_id IN ({placeholders(asset_ids)})",
        asset_ids,
    )
    counts = {}
    for (labels_json,) in cur.fetchall():
        labels = parse_json(labels_json, [])
        if not isinstance(labels, list):
            continue
        for label in labels:
            counts[str(label)] = counts.get(str(label), 0) + 1
    return dict(sorted(counts.items()))


def content_labeled_asset_count(cur, asset_ids: list[str]) -> int:
    """Count assets that have at least one content-filter label."""
    if not asset_ids:
        return 0
    cur.execute(
        "SELECT content_labels_json FROM semantic_analysis "
        f"WHERE asset_id IN ({placeholders(asset_ids)})",
        asset_ids,
    )
    count = 0
    for (labels_json,) in cur.fetchall():
        labels = parse_json(labels_json, [])
        if isinstance(labels, list) and labels:
            count += 1
    return count


def filter_reason_counts(cur, bucket: str | None) -> dict[str, int]:
    """Count filtering reasons for one album bucket or all buckets."""
    if bucket is None:
        cur.execute("SELECT reason, COUNT(*) FROM asset_filter_results GROUP BY reason")
    else:
        cur.execute(
            "SELECT reason, COUNT(*) FROM asset_filter_results "
            "WHERE album_bucket = ? GROUP BY reason",
            (bucket,),
        )
    return {str(reason): count for reason, count in cur.fetchall()}


def duplicate_stats(cur, bucket: str | None) -> dict:
    """Count duplicate groups and suppressed members."""
    if bucket is None:
        cur.execute("SELECT group_id, reason FROM duplicate_groups")
    else:
        cur.execute(
            "SELECT group_id, reason FROM duplicate_groups WHERE album_bucket = ?",
            (bucket,),
        )
    groups = cur.fetchall()
    if not groups:
        return {"groups": 0, "suppressed": 0, "reasons": {}}

    group_ids = [group_id for group_id, _reason in groups]
    cur.execute(
        "SELECT group_id, COUNT(*) FROM duplicate_group_members "
        f"WHERE group_id IN ({placeholders(group_ids)}) GROUP BY group_id",
        group_ids,
    )
    member_counts = {group_id: count for group_id, count in cur.fetchall()}
    reasons = {}
    for _group_id, reason in groups:
        reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "groups": len(groups),
        "suppressed": sum(
            max(0, member_counts.get(group_id, 0) - 1) for group_id in group_ids
        ),
        "reasons": dict(sorted(reasons.items())),
    }


def selected_asset_count(albums: list[dict], bucket: str | None) -> int:
    """Count selected/generated album assets for one bucket or all buckets."""
    if bucket is None:
        return sum(len(album.get("asset_ids", [])) for album in albums)
    for album in albums:
        if album.get("bucket") == bucket:
            return len(album.get("asset_ids", []))
    return 0


def build_pipeline_summary_for_bucket(
    cur,
    albums: list[dict],
    bucket: str | None,
    label: str,
) -> dict:
    """Build summary cards for one album bucket or the aggregate view."""
    asset_ids = unique_asset_ids_for_bucket(cur, bucket)
    candidate_count = (
        query_single_value(cur, "SELECT COUNT(*) FROM asset_filter_results")
        if bucket is None
        else query_single_value(
            cur,
            "SELECT COUNT(*) FROM asset_filter_results WHERE album_bucket = ?",
            (bucket,),
        )
    )
    accepted_count = (
        query_single_value(
            cur,
            "SELECT COUNT(*) FROM asset_filter_results WHERE included = 1",
        )
        if bucket is None
        else query_single_value(
            cur,
            "SELECT COUNT(*) FROM asset_filter_results "
            "WHERE album_bucket = ? AND included = 1",
            (bucket,),
        )
    )
    rejected_count = candidate_count - accepted_count
    technical_count = count_for_assets(cur, "technical_analysis", asset_ids)
    semantic_count = count_for_assets(cur, "semantic_analysis", asset_ids)
    scores = score_stats_for_assets(cur, asset_ids)
    labels = content_label_counts(cur, asset_ids)
    labeled_asset_count = content_labeled_asset_count(cur, asset_ids)
    duplicates = duplicate_stats(cur, bucket)

    stages = [
        {
            "title": "Asset Discovery",
            "metrics": {
                "Candidates found": candidate_count,
                "Unique assets": len(asset_ids),
            },
        },
        {
            "title": "Filtering",
            "metrics": {
                "Accepted": accepted_count,
                "Rejected": rejected_count,
            },
            "details": filter_reason_counts(cur, bucket),
        },
        {
            "title": "Technical Analysis",
            "metrics": {
                "Analyzed": technical_count,
                "With pHash": count_for_assets(
                    cur,
                    "technical_analysis",
                    asset_ids,
                    "phash IS NOT NULL AND phash != ''",
                ),
            },
        },
        {
            "title": "Semantic Analysis",
            "metrics": {
                "Analyzed": semantic_count,
                "With faces": count_for_assets(
                    cur,
                    "semantic_analysis",
                    asset_ids,
                    "face_count > 0",
                ),
                "With location": count_for_assets(
                    cur,
                    "semantic_analysis",
                    asset_ids,
                    "has_location = 1",
                ),
                "With rating": count_for_assets(
                    cur,
                    "semantic_analysis",
                    asset_ids,
                    "rating IS NOT NULL",
                ),
            },
        },
        {
            "title": "Content Filters",
            "metrics": {
                "Matched assets": labeled_asset_count,
                "Label hits": sum(labels.values()),
            },
            "details": labels,
        },
        {
            "title": "Scoring",
            "metrics": {
                "Scored": scores["count"],
                "Average score": scores["average"],
                "Highest score": scores["highest"],
                "Lowest score": scores["lowest"],
            },
        },
        {
            "title": "Duplicate Detection",
            "metrics": {
                "Groups": duplicates["groups"],
                "Suppressed": duplicates["suppressed"],
            },
            "details": duplicates["reasons"],
        },
        {
            "title": "Album Selection",
            "metrics": {
                "Selected": selected_asset_count(albums, bucket),
            },
        },
    ]
    return {"label": label, "stages": stages}


def load_pipeline_summaries(db_path: str, albums: list[dict]) -> dict[str, dict]:
    """Load pipeline summary cards for all albums and each album bucket."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    summaries = {
        "all": build_pipeline_summary_for_bucket(cur, albums, None, "All albums")
    }
    for album in albums:
        summaries[album["bucket"]] = build_pipeline_summary_for_bucket(
            cur,
            albums,
            album["bucket"],
            album["name"],
        )
    conn.close()
    return summaries


def attach_album_memberships(assets: list[dict], memberships: dict[str, list[dict]]):
    """Annotate scored assets with generated album memberships."""
    for asset in assets:
        asset["albums"] = memberships.get(asset["asset_id"], [])


def attach_duplicate_memberships(
    assets: list[dict],
    memberships: dict[str, list[dict]],
):
    """Annotate scored assets with duplicate detection status."""
    for asset in assets:
        asset["duplicates"] = memberships.get(asset["asset_id"], [])


def attach_pipeline_statuses(
    assets: list[dict],
    albums: list[dict],
    filter_memberships: dict[str, list[dict]],
):
    """Attach per-album pipeline status rows to each scored asset."""
    album_names = {album["bucket"]: album["name"] for album in albums}
    for asset in assets:
        album_buckets = {album["bucket"] for album in asset.get("albums", [])}
        duplicate_buckets = {
            duplicate["bucket"] for duplicate in asset.get("duplicates", [])
        }
        filter_by_bucket = {
            item["bucket"]: item
            for item in filter_memberships.get(asset["asset_id"], [])
        }
        buckets = sorted(album_buckets | duplicate_buckets | set(filter_by_bucket))
        statuses = []
        for bucket in buckets:
            filter_row = filter_by_bucket.get(bucket)
            duplicate_rows = [
                duplicate
                for duplicate in asset.get("duplicates", [])
                if duplicate.get("bucket") == bucket
            ]
            duplicate_text = "not duplicate"
            if duplicate_rows:
                duplicate_text = ", ".join(
                    f"{row['role']} d={row['distance']}" for row in duplicate_rows
                )
            if filter_row:
                filter_text = (
                    f"accepted: {filter_row['reason']}"
                    if filter_row["included"]
                    else f"rejected: {filter_row['reason']}"
                )
            else:
                filter_text = "no candidate record"
            statuses.append(
                {
                    "album": album_names.get(bucket, bucket),
                    "bucket": bucket,
                    "candidate": "yes" if filter_row else "no",
                    "filter": filter_text,
                    "duplicate": duplicate_text,
                    "selection": (
                        "selected" if bucket in album_buckets else "not selected"
                    ),
                }
            )
        if not statuses:
            statuses.append(
                {
                    "album": "Current albums",
                    "bucket": "",
                    "candidate": "no",
                    "filter": "no candidate record",
                    "duplicate": "not duplicate",
                    "selection": "not selected",
                }
            )
        asset["pipeline_statuses"] = statuses


def format_value(value) -> str:
    """Format scalar values compactly for the report."""
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def render_score_items(items: dict) -> str:
    """Render a compact definition list for score components or inputs."""
    if not items:
        return "<p class='muted'>No data</p>"
    rows = []
    for key, value in sorted(items.items()):
        rows.append(
            "<div class='score-row'>"
            f"<span>{html.escape(str(key))}</span>"
            f"<strong>{html.escape(format_value(value))}</strong>"
            "</div>"
        )
    return "\n".join(rows)


def render_content_filter_badges(inputs: dict) -> str:
    """Render compact badges for smart-search content filter matches."""
    matches = inputs.get("content_filter_matches") or []
    if not matches:
        return ""
    badges = []
    for match in matches:
        label = html.escape(str(match.get("label", "content-filter")))
        penalty = html.escape(str(match.get("penalty", "")))
        query = html.escape(str(match.get("query", "")))
        rank = match.get("rank")
        rank_text = f" rank {rank}" if rank else ""
        title = f' title="Smart search: {query}{rank_text}"' if query else ""
        penalty_text = f" {penalty}" if penalty else ""
        visible_rank = f" #{rank}" if rank else ""
        badges.append(f"<span{title}>{label}{visible_rank}{penalty_text}</span>")
    return f"""
      <div class="content-filters">
        <strong>Content filters</strong>
        <div>{''.join(badges)}</div>
      </div>
    """


def content_filter_labels(asset: dict) -> list[str]:
    """Return unique content filter labels stored for one asset."""
    inputs = asset.get("score_details", {}).get("inputs", {})
    matches = inputs.get("content_filter_matches") or []
    labels = []
    for match in matches:
        label = match.get("label") if isinstance(match, dict) else None
        if label and label not in labels:
            labels.append(label)
    return labels


def duplicate_roles(asset: dict) -> list[str]:
    """Return duplicate roles attached to one asset."""
    roles = []
    for membership in asset.get("duplicates", []):
        role = membership.get("role") if isinstance(membership, dict) else None
        if role and role not in roles:
            roles.append(role)
    return roles


def render_duplicate_badges(asset: dict) -> str:
    """Render duplicate status badges for one asset card."""
    duplicates = asset.get("duplicates") or []
    if not duplicates:
        return ""
    badges = []
    for duplicate in duplicates:
        role = html.escape(str(duplicate.get("role", "duplicate")))
        reason = str(duplicate.get("reason", ""))
        distance = duplicate.get("distance")
        representative_id = str(duplicate.get("representative_asset_id", ""))
        title_parts = []
        if reason:
            title_parts.append(reason)
        if representative_id:
            title_parts.append(f"representative: {representative_id}")
        title = (
            f' title="{html.escape("; ".join(title_parts), quote=True)}"'
            if title_parts
            else ""
        )
        distance_text = f" d={distance}" if distance is not None else ""
        badges.append(f"<span{title}>{role}{distance_text}</span>")
    return f"""
      <div class="duplicate-status">
        <strong>Duplicates</strong>
        <div>{''.join(badges)}</div>
      </div>
    """


def render_pipeline_status(asset: dict) -> str:
    """Render per-album pipeline status rows for one asset card."""
    rows = []
    for status in asset.get("pipeline_statuses", []):
        album = html.escape(str(status.get("album", "Album")))
        candidate = html.escape(str(status.get("candidate", "unknown")))
        filter_text = html.escape(str(status.get("filter", "unknown")))
        duplicate = html.escape(str(status.get("duplicate", "unknown")))
        selection = html.escape(str(status.get("selection", "unknown")))
        rows.append(
            "<div class='pipeline-status-row'>"
            f"<strong>{album}</strong>"
            f"<span>Candidate: {candidate}</span>"
            f"<span>Filter: {filter_text}</span>"
            f"<span>Duplicate: {duplicate}</span>"
            f"<span>Selection: {selection}</span>"
            "</div>"
        )
    return f"""
      <details class="pipeline-status" open>
        <summary>Pipeline status</summary>
        {''.join(rows)}
      </details>
    """


def render_asset_card(asset: dict, immich_url: str) -> str:
    """Render one scored asset review card."""
    asset_id = asset["asset_id"]
    score_details = asset.get("score_details", {})
    components = score_details.get("components", {})
    inputs = score_details.get("inputs", {})
    faces_json = json.dumps(inputs.get("faces", []))
    dimensions_json = json.dumps(inputs.get("dimensions", []))
    albums = asset.get("albums", [])
    album_buckets_json = json.dumps([album["bucket"] for album in albums])
    content_filter_labels_json = json.dumps(content_filter_labels(asset))
    duplicate_roles_json = json.dumps(duplicate_roles(asset))
    link = immich_asset_url(immich_url, asset_id)
    thumbnail = asset.get("thumbnail_src") or immich_thumbnail_url(immich_url, asset_id)
    escaped_id = html.escape(asset_id)
    escaped_link = html.escape(link, quote=True)
    escaped_thumbnail = html.escape(thumbnail, quote=True)
    escaped_faces = html.escape(faces_json, quote=True)
    escaped_dimensions = html.escape(dimensions_json, quote=True)
    escaped_album_buckets = html.escape(album_buckets_json, quote=True)
    escaped_content_filter_labels = html.escape(content_filter_labels_json, quote=True)
    escaped_duplicate_roles = html.escape(duplicate_roles_json, quote=True)
    album_badges = "".join(
        f"<span>{html.escape(album['name'])}</span>" for album in albums
    )
    if not album_badges:
        album_badges = "<span>Not in generated album</span>"
    content_filter_badges = render_content_filter_badges(inputs)
    duplicate_badges = render_duplicate_badges(asset)
    pipeline_status = render_pipeline_status(asset)
    escaped_datetime = html.escape(
        str(asset.get("photo_datetime") or "Unknown datetime")
    )
    return f"""
    <article
      class="card"
      data-asset-id="{escaped_id}"
      data-faces="{escaped_faces}"
      data-dimensions="{escaped_dimensions}"
      data-albums="{escaped_album_buckets}"
      data-content-filters="{escaped_content_filter_labels}"
      data-duplicate-roles="{escaped_duplicate_roles}"
    >
      <a class="thumb-link" href="{escaped_link}" target="_blank" rel="noreferrer">
        <img
          class="thumbnail"
          src="{escaped_thumbnail}"
          alt="Thumbnail for {escaped_id}"
          loading="lazy"
        >
        <div class="face-overlay" aria-hidden="true"></div>
      </a>
      <header>
        <div>
          <a class="asset-link" href="{escaped_link}" target="_blank" rel="noreferrer">
            Open in Immich
          </a>
          <div class="asset-datetime">{escaped_datetime}</div>
        </div>
        <div class="score">{html.escape(str(asset["score"]))}</div>
      </header>
      <div class="album-badges">
        {album_badges}
      </div>
      {content_filter_badges}
      {duplicate_badges}
      {pipeline_status}

      <section class="labels">
        <label>
          My score
          <select data-field="my_score">
            <option value="">Unrated</option>
            <option value="1">1 - reject</option>
            <option value="2">2</option>
            <option value="3">3 - ok</option>
            <option value="4">4</option>
            <option value="5">5 - highlight</option>
          </select>
        </label>
        <label>
          Include
          <select data-field="include">
            <option value="">Unsure</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </label>
      </section>

      <details class="score-components">
        <summary>Score components</summary>
        {render_score_items(components)}
      </details>
      <details class="scoring-inputs">
        <summary>Scoring inputs</summary>
        {render_score_items(inputs)}
      </details>
    </article>
    """


def render_album_filter_options(albums: list[dict]) -> str:
    """Render album filter dropdown options."""
    options = ['<option value="all">All scored assets</option>']
    for album in albums:
        options.append(
            f'<option value="{html.escape(album["bucket"], quote=True)}">'
            f'{html.escape(album["name"])}</option>'
        )
    options.append('<option value="none">Not in generated album</option>')
    return "\n".join(options)


def render_content_filter_options(assets: list[dict]) -> str:
    """Render dropdown options for labels found in score details."""
    labels = sorted(
        {label for asset in assets for label in content_filter_labels(asset)}
    )
    options = ['<option value="all">All content labels</option>']
    options.append('<option value="any">Has any content label</option>')
    for label in labels:
        escaped_label = html.escape(label, quote=True)
        options.append(f'<option value="{escaped_label}">{html.escape(label)}</option>')
    options.append('<option value="none">No content label</option>')
    return "\n".join(options)


def render_duplicate_filter_options() -> str:
    """Render dropdown options for duplicate detection status."""
    return "\n".join(
        [
            '<option value="all">All duplicate statuses</option>',
            '<option value="any">In any duplicate group</option>',
            '<option value="suppressed">Suppressed duplicates</option>',
            '<option value="representative">Duplicate representatives</option>',
            '<option value="none">Not in duplicate group</option>',
        ]
    )


def render_pipeline_summary_shell() -> str:
    """Render the empty pipeline summary section filled by JavaScript."""
    return """
    <section class="pipeline-summary" aria-labelledby="pipeline-summary-title">
      <div class="summary-heading">
        <div>
          <h2 id="pipeline-summary-title">Pipeline Summary</h2>
          <p class="muted" id="pipeline-summary-label">All albums</p>
        </div>
      </div>
      <div class="stage-grid" id="pipeline-summary-grid"></div>
    </section>
    """


def render_review_html(
    assets: list[dict],
    immich_url: str,
    albums: list[dict] | None = None,
    pipeline_summaries: dict[str, dict] | None = None,
) -> str:
    """Render a full static review page."""
    albums = albums or []
    pipeline_summaries = pipeline_summaries or {}
    cards = "\n".join(render_asset_card(asset, immich_url) for asset in assets)
    album_options = render_album_filter_options(albums)
    content_filter_options = render_content_filter_options(assets)
    duplicate_filter_options = render_duplicate_filter_options()
    summary_json = json.dumps(pipeline_summaries).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Immich Highlights Review</title>
  <style>
    :root {{
      color-scheme: light dark;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    body {{
      margin: 0;
      background: #f6f7f9;
      color: #172033;
    }}
    main {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    .muted {{
      color: #657187;
    }}
    .grid {{
      display: grid;
      gap: 16px;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      margin-top: 24px;
    }}
    .card {{
      background: white;
      border: 1px solid #dfe4ec;
      border-radius: 8px;
      padding: 16px;
      box-shadow: 0 1px 2px rgb(0 0 0 / 0.04);
    }}
    .pipeline-summary {{
      margin-top: 22px;
      padding-top: 18px;
      border-top: 1px solid #dfe4ec;
    }}
    .summary-heading {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
    }}
    .summary-heading h2 {{
      margin: 0;
      font-size: 22px;
    }}
    .stage-grid {{
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      margin-top: 14px;
    }}
    .stage-card {{
      background: white;
      border: 1px solid #dfe4ec;
      border-radius: 8px;
      padding: 14px;
      box-shadow: 0 1px 2px rgb(0 0 0 / 0.04);
    }}
    .stage-card h3 {{
      margin: 0 0 10px;
      font-size: 15px;
    }}
    .stage-metric,
    .stage-detail {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 4px 0;
      border-bottom: 1px solid #f0f2f6;
      font-size: 13px;
    }}
    .stage-detail {{
      color: #657187;
      font-size: 12px;
    }}
    .stage-metric:last-child,
    .stage-detail:last-child {{
      border-bottom: 0;
    }}
    .stage-metric strong,
    .stage-detail strong {{
      text-align: right;
    }}
    .card.hidden {{
      display: none;
    }}
    .thumb-link {{
      display: block;
      position: relative;
      margin: -4px -4px 14px;
      overflow: hidden;
      border-radius: 6px;
      background: #e8edf5;
    }}
    .thumbnail {{
      display: block;
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: contain;
    }}
    .face-overlay {{
      position: absolute;
      inset: 0;
      pointer-events: none;
      display: none;
    }}
    body.show-face-overlays .face-overlay {{
      display: block;
    }}
    .face-box {{
      position: absolute;
      border: 3px solid #19c37d;
      border-radius: 4px;
      box-shadow: 0 0 0 1px rgb(0 0 0 / 0.55);
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      margin-top: 16px;
    }}
    .album-filter {{
      min-width: 240px;
    }}
    .content-filter {{
      min-width: 220px;
    }}
    .duplicate-filter {{
      min-width: 230px;
    }}
    button {{
      border: 1px solid #cbd3df;
      border-radius: 6px;
      background: white;
      color: #172033;
      cursor: pointer;
      font: inherit;
      font-weight: 800;
      padding: 8px 12px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
    }}
    .asset-link {{
      color: #1251a3;
      font-weight: 700;
      text-decoration: none;
    }}
    .asset-link:hover {{
      text-decoration: underline;
    }}
    .asset-datetime {{
      margin-top: 4px;
      color: #657187;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .score {{
      min-width: 56px;
      border-radius: 8px;
      background: #172033;
      color: white;
      font-size: 28px;
      font-weight: 800;
      line-height: 1;
      padding: 10px;
      text-align: center;
    }}
    .album-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 12px;
    }}
    .album-badges span {{
      border-radius: 999px;
      background: #e8edf5;
      color: #3d485b;
      font-size: 12px;
      font-weight: 700;
      padding: 4px 8px;
    }}
    .content-filters {{
      display: grid;
      gap: 6px;
      margin-top: 10px;
      color: #8a3b12;
      font-size: 12px;
      font-weight: 800;
    }}
    .content-filters div {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .content-filters span {{
      border-radius: 999px;
      background: #fff1e5;
      color: #8a3b12;
      padding: 4px 8px;
    }}
    .duplicate-status {{
      display: grid;
      gap: 6px;
      margin-top: 10px;
      color: #5d3fd3;
      font-size: 12px;
      font-weight: 800;
    }}
    .duplicate-status div {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .duplicate-status span {{
      border-radius: 999px;
      background: #eee9ff;
      color: #5d3fd3;
      padding: 4px 8px;
    }}
    .pipeline-status {{
      border: 1px solid #edf0f5;
      border-radius: 6px;
      padding: 10px;
      margin-top: 12px;
      background: #fbfcfe;
    }}
    .pipeline-status summary {{
      margin-bottom: 8px;
    }}
    .pipeline-status-row {{
      display: grid;
      gap: 4px;
      padding: 8px 0;
      border-top: 1px solid #edf0f5;
      font-size: 12px;
    }}
    .pipeline-status-row:first-of-type {{
      border-top: 0;
      padding-top: 0;
    }}
    .pipeline-status-row strong {{
      font-size: 13px;
    }}
    .pipeline-status-row span {{
      color: #4c586c;
      overflow-wrap: anywhere;
    }}
    .labels {{
      display: grid;
      gap: 10px;
      margin: 16px 0;
    }}
    label {{
      display: grid;
      gap: 4px;
      color: #3d485b;
      font-size: 13px;
      font-weight: 700;
    }}
    select {{
      box-sizing: border-box;
      width: 100%;
      border: 1px solid #cbd3df;
      border-radius: 6px;
      padding: 8px;
      font: inherit;
    }}
    details {{
      border-top: 1px solid #edf0f5;
      padding-top: 10px;
      margin-top: 10px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 800;
    }}
    .score-row {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 5px 0;
      border-bottom: 1px solid #f0f2f6;
      font-size: 13px;
    }}
    .score-row span {{
      color: #4c586c;
      overflow-wrap: anywhere;
    }}
    .score-row strong {{
      max-width: 55%;
      text-align: right;
      overflow-wrap: anywhere;
    }}
    @media (prefers-color-scheme: dark) {{
      body {{
        background: #10141c;
        color: #edf2fb;
      }}
      .card {{
        background: #171d29;
        border-color: #30394a;
      }}
      .pipeline-summary {{
        border-color: #30394a;
      }}
      .stage-card {{
        background: #171d29;
        border-color: #30394a;
      }}
      .thumb-link {{
        background: #252d3d;
      }}
      .score {{
        background: #edf2fb;
        color: #10141c;
      }}
      .muted,
      .asset-datetime,
      label,
      .score-row span {{
        color: #a8b3c6;
      }}
      .album-badges span {{
        background: #252d3d;
        color: #c8d2e4;
      }}
      .content-filters {{
        color: #ffb07c;
      }}
      .content-filters span {{
        background: #3a281d;
        color: #ffb07c;
      }}
      .duplicate-status {{
        color: #b9a8ff;
      }}
      .duplicate-status span {{
        background: #27213d;
        color: #b9a8ff;
      }}
      .pipeline-status {{
        background: #121824;
        border-color: #2a3344;
      }}
      .pipeline-status-row {{
        border-color: #2a3344;
      }}
      .pipeline-status-row span {{
        color: #a8b3c6;
      }}
      select,
      button {{
        background: #10141c;
        border-color: #3c4658;
        color: #edf2fb;
      }}
      details,
      .score-row,
      .stage-metric,
      .stage-detail {{
        border-color: #2a3344;
      }}
      .asset-link {{
        color: #8ab4ff;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>Immich Highlights Review</h1>
    <p class="muted">
      <span id="visible-count">{len(assets)}</span> of {len(assets)} scored assets.
      Labels are stored only in this browser's
      local storage. Use the Immich links to inspect photos, then compare your
      judgement with the score components.
    </p>
    <div class="toolbar">
      <label class="album-filter">
        Album
        <select id="album-filter">
          {album_options}
        </select>
      </label>
      <label class="content-filter">
        Content label
        <select id="content-filter">
          {content_filter_options}
        </select>
      </label>
      <label class="duplicate-filter">
        Duplicate status
        <select id="duplicate-filter">
          {duplicate_filter_options}
        </select>
      </label>
      <button id="toggle-components" type="button">Show score components</button>
      <button id="toggle-inputs" type="button">Show scoring inputs</button>
      <button id="toggle-faces" type="button">Show face boxes</button>
    </div>
    {render_pipeline_summary_shell()}
    <section class="grid">
      {cards}
    </section>
  </main>
  <script>
    const prefix = "immich-highlights-review:";
    const faceToggleKey = prefix + "show-face-overlays";
    const albumFilterKey = prefix + "album-filter";
    const contentFilterKey = prefix + "content-filter";
    const duplicateFilterKey = prefix + "duplicate-filter";
    const componentsToggleKey = prefix + "show-score-components";
    const inputsToggleKey = prefix + "show-scoring-inputs";
    const faceButton = document.querySelector("#toggle-faces");
    const componentsButton = document.querySelector("#toggle-components");
    const inputsButton = document.querySelector("#toggle-inputs");
    const albumFilter = document.querySelector("#album-filter");
    const contentFilter = document.querySelector("#content-filter");
    const duplicateFilter = document.querySelector("#duplicate-filter");
    const visibleCount = document.querySelector("#visible-count");
    const pipelineSummaries = {summary_json};
    const pipelineSummaryLabel = document.querySelector("#pipeline-summary-label");
    const pipelineSummaryGrid = document.querySelector("#pipeline-summary-grid");

    function parseJsonAttribute(element, name, fallback) {{
      try {{
        return JSON.parse(element.dataset[name] || "");
      }} catch {{
        return fallback;
      }}
    }}

    function imageArea(container, dimensions) {{
      const imageWidth = Number(dimensions[0]);
      const imageHeight = Number(dimensions[1]);
      if (!imageWidth || !imageHeight) {{
        return null;
      }}

      const containerWidth = container.clientWidth;
      const containerHeight = container.clientHeight;
      const imageAspect = imageWidth / imageHeight;
      const containerAspect = containerWidth / containerHeight;

      if (containerAspect > imageAspect) {{
        const height = containerHeight;
        const width = height * imageAspect;
        return {{ x: (containerWidth - width) / 2, y: 0, width, height }};
      }}

      const width = containerWidth;
      const height = width / imageAspect;
      return {{ x: 0, y: (containerHeight - height) / 2, width, height }};
    }}

    function renderFaceBoxes(card) {{
      const faces = parseJsonAttribute(card, "faces", []);
      const dimensions = parseJsonAttribute(card, "dimensions", []);
      const container = card.querySelector(".thumb-link");
      const overlay = card.querySelector(".face-overlay");
      const area = imageArea(container, dimensions);
      overlay.innerHTML = "";
      if (!area) {{
        return;
      }}

      const imageWidth = Number(dimensions[0]);
      const imageHeight = Number(dimensions[1]);
      for (const face of faces) {{
        const box = document.createElement("div");
        box.className = "face-box";
        box.style.left = `${{area.x + (face.x / imageWidth) * area.width}}px`;
        box.style.top = `${{area.y + (face.y / imageHeight) * area.height}}px`;
        box.style.width = `${{(face.width / imageWidth) * area.width}}px`;
        box.style.height = `${{(face.height / imageHeight) * area.height}}px`;
        overlay.append(box);
      }}
    }}

    function renderAllFaceBoxes() {{
      for (const card of document.querySelectorAll(".card")) {{
        renderFaceBoxes(card);
      }}
    }}

    function setFaceOverlayVisibility(show) {{
      document.body.classList.toggle("show-face-overlays", show);
      faceButton.textContent = show ? "Hide face boxes" : "Show face boxes";
      localStorage.setItem(faceToggleKey, show ? "true" : "false");
      renderAllFaceBoxes();
    }}

    function setDetailsVisibility(selector, button, show, shownText, hiddenText, key) {{
      for (const details of document.querySelectorAll(selector)) {{
        details.open = show;
      }}
      button.textContent = show ? shownText : hiddenText;
      localStorage.setItem(key, show ? "true" : "false");
    }}

    function toggleDetails(selector, button, shownText, hiddenText, key) {{
      const anyClosed = Array.from(
        document.querySelectorAll(selector)
      ).some((details) => !details.open);
      setDetailsVisibility(selector, button, anyClosed, shownText, hiddenText, key);
    }}

    function cardMatchesAlbum(card, selectedAlbum) {{
      if (selectedAlbum === "all") {{
        return true;
      }}

      const albums = parseJsonAttribute(card, "albums", []);
      if (selectedAlbum === "none") {{
        return albums.length === 0;
      }}
      return albums.includes(selectedAlbum);
    }}

    function cardMatchesContentFilter(card, selectedContentFilter) {{
      if (selectedContentFilter === "all") {{
        return true;
      }}

      const contentFilters = parseJsonAttribute(card, "contentFilters", []);
      if (selectedContentFilter === "any") {{
        return contentFilters.length > 0;
      }}
      if (selectedContentFilter === "none") {{
        return contentFilters.length === 0;
      }}
      return contentFilters.includes(selectedContentFilter);
    }}

    function cardMatchesDuplicateFilter(card, selectedDuplicateFilter) {{
      if (selectedDuplicateFilter === "all") {{
        return true;
      }}

      const duplicateRoles = parseJsonAttribute(card, "duplicateRoles", []);
      if (selectedDuplicateFilter === "any") {{
        return duplicateRoles.length > 0;
      }}
      if (selectedDuplicateFilter === "none") {{
        return duplicateRoles.length === 0;
      }}
      return duplicateRoles.includes(selectedDuplicateFilter);
    }}

    function renderSummaryRows(items, className) {{
      return Object.entries(items || {{}}).map(([label, value]) => `
        <div class="${{className}}">
          <span>${{escapeHtml(label)}}</span>
          <strong>${{escapeHtml(String(value))}}</strong>
        </div>
      `).join("");
    }}

    function escapeHtml(value) {{
      return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function renderPipelineSummary(selectedAlbum) {{
      const summary = pipelineSummaries[selectedAlbum] || pipelineSummaries.all;
      if (!summary) {{
        pipelineSummaryLabel.textContent = "No pipeline data available";
        pipelineSummaryGrid.innerHTML = "";
        return;
      }}

      pipelineSummaryLabel.textContent = summary.label || "Pipeline";
      pipelineSummaryGrid.innerHTML = (summary.stages || []).map((stage) => `
        <article class="stage-card">
          <h3>${{escapeHtml(stage.title)}}</h3>
          ${{renderSummaryRows(stage.metrics, "stage-metric")}}
          ${{renderSummaryRows(stage.details, "stage-detail")}}
        </article>
      `).join("");
    }}

    function applyFilters() {{
      const selectedAlbum = albumFilter.value || "all";
      const selectedContentFilter = contentFilter.value || "all";
      const selectedDuplicateFilter = duplicateFilter.value || "all";
      localStorage.setItem(albumFilterKey, selectedAlbum);
      localStorage.setItem(contentFilterKey, selectedContentFilter);
      localStorage.setItem(duplicateFilterKey, selectedDuplicateFilter);
      let count = 0;
      for (const card of document.querySelectorAll(".card")) {{
        const visible = cardMatchesAlbum(card, selectedAlbum)
          && cardMatchesContentFilter(card, selectedContentFilter)
          && cardMatchesDuplicateFilter(card, selectedDuplicateFilter);
        card.classList.toggle("hidden", !visible);
        if (visible) {{
          count += 1;
        }}
      }}
      visibleCount.textContent = String(count);
      renderPipelineSummary(selectedAlbum);
      renderAllFaceBoxes();
    }}

    faceButton.addEventListener("click", () => {{
      setFaceOverlayVisibility(!document.body.classList.contains("show-face-overlays"));
    }});
    componentsButton.addEventListener("click", () => {{
      toggleDetails(
        ".score-components",
        componentsButton,
        "Hide score components",
        "Show score components",
        componentsToggleKey
      );
    }});
    inputsButton.addEventListener("click", () => {{
      toggleDetails(
        ".scoring-inputs",
        inputsButton,
        "Hide scoring inputs",
        "Show scoring inputs",
        inputsToggleKey
      );
    }});
    albumFilter.value = localStorage.getItem(albumFilterKey) || "all";
    if (!albumFilter.value) {{
      albumFilter.value = "all";
    }}
    contentFilter.value = localStorage.getItem(contentFilterKey) || "all";
    if (!contentFilter.value) {{
      contentFilter.value = "all";
    }}
    duplicateFilter.value = localStorage.getItem(duplicateFilterKey) || "all";
    if (!duplicateFilter.value) {{
      duplicateFilter.value = "all";
    }}
    albumFilter.addEventListener("change", applyFilters);
    contentFilter.addEventListener("change", applyFilters);
    duplicateFilter.addEventListener("change", applyFilters);
    window.addEventListener("resize", renderAllFaceBoxes);

    for (const card of document.querySelectorAll(".card")) {{
      const assetId = card.dataset.assetId;
      const image = card.querySelector(".thumbnail");
      image.addEventListener("load", () => renderFaceBoxes(card));
      renderFaceBoxes(card);

      for (const field of card.querySelectorAll("[data-field]")) {{
        const key = prefix + assetId + ":" + field.dataset.field;
        field.value = localStorage.getItem(key) || "";
        field.addEventListener("input", () => {{
          localStorage.setItem(key, field.value);
        }});
      }}
    }}
    setFaceOverlayVisibility(localStorage.getItem(faceToggleKey) === "true");
    setDetailsVisibility(
      ".score-components",
      componentsButton,
      localStorage.getItem(componentsToggleKey) === "true",
      "Hide score components",
      "Show score components",
      componentsToggleKey
    );
    setDetailsVisibility(
      ".scoring-inputs",
      inputsButton,
      localStorage.getItem(inputsToggleKey) === "true",
      "Hide scoring inputs",
      "Show scoring inputs",
      inputsToggleKey
    );
    applyFilters();
  </script>
</body>
</html>
"""


def write_review_html(
    db_path: str,
    immich_url: str,
    output_path: str,
    limit: int | None = None,
    api_key: str = "",
    download_thumbnails: bool = True,
) -> Path:
    """Write the review report and return the output path."""
    assets = load_processed_assets(db_path, limit=limit)
    albums, memberships = load_album_memberships(db_path)
    duplicate_memberships = load_duplicate_memberships(db_path)
    filter_memberships = load_filter_memberships(db_path)
    pipeline_summaries = load_pipeline_summaries(db_path, albums)
    attach_album_memberships(assets, memberships)
    attach_duplicate_memberships(assets, duplicate_memberships)
    attach_pipeline_statuses(assets, albums, filter_memberships)
    if download_thumbnails:
        attach_local_thumbnails(
            assets,
            immich_url,
            api_key,
            output_path,
        )
    html_text = render_review_html(
        assets,
        immich_url,
        albums=albums,
        pipeline_summaries=pipeline_summaries,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")
    return path


def count_local_thumbnails(output_path: str) -> int:
    """Count locally downloaded thumbnails beside a report."""
    thumbnail_dir = Path(output_path).parent / "thumbnails"
    if not thumbnail_dir.exists():
        return 0
    return len(list(thumbnail_dir.glob("*.jpg")))


def parse_args():
    """Parse command line arguments for the development-only exporter."""
    parser = argparse.ArgumentParser(
        description="Export a static HTML scoring review report."
    )
    parser.add_argument("--db", default=os.getenv("SCORER_DB_PATH", DEFAULT_DB_PATH))
    parser.add_argument(
        "--immich-url",
        default=os.getenv("IMMICH_API_URL", DEFAULT_IMMICH_URL),
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--api-key", default=os.getenv("IMMICH_API_KEY", ""))
    parser.add_argument(
        "--no-download-thumbnails",
        action="store_true",
        help="Use remote Immich thumbnail URLs instead of local thumbnail files.",
    )
    return parser.parse_args()


def main():
    """CLI entrypoint for local development review exports."""
    if load_dotenv:
        load_dotenv()
    args = parse_args()
    path = write_review_html(
        db_path=args.db,
        immich_url=args.immich_url,
        output_path=args.output,
        limit=args.limit,
        api_key=args.api_key,
        download_thumbnails=not args.no_download_thumbnails,
    )
    print(f"Wrote review report to {path}")
    if not args.no_download_thumbnails:
        print(f"Local thumbnails available: {count_local_thumbnails(args.output)}")


if __name__ == "__main__":
    main()
