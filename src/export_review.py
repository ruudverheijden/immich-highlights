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


def attach_album_memberships(assets: list[dict], memberships: dict[str, list[dict]]):
    """Annotate scored assets with generated album memberships."""
    for asset in assets:
        asset["albums"] = memberships.get(asset["asset_id"], [])


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
    link = immich_asset_url(immich_url, asset_id)
    thumbnail = asset.get("thumbnail_src") or immich_thumbnail_url(immich_url, asset_id)
    escaped_id = html.escape(asset_id)
    escaped_link = html.escape(link, quote=True)
    escaped_thumbnail = html.escape(thumbnail, quote=True)
    escaped_faces = html.escape(faces_json, quote=True)
    escaped_dimensions = html.escape(dimensions_json, quote=True)
    escaped_album_buckets = html.escape(album_buckets_json, quote=True)
    escaped_content_filter_labels = html.escape(content_filter_labels_json, quote=True)
    album_badges = "".join(
        f"<span>{html.escape(album['name'])}</span>" for album in albums
    )
    if not album_badges:
        album_badges = "<span>Not in generated album</span>"
    content_filter_badges = render_content_filter_badges(inputs)
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


def render_review_html(
    assets: list[dict],
    immich_url: str,
    albums: list[dict] | None = None,
) -> str:
    """Render a full static review page."""
    albums = albums or []
    cards = "\n".join(render_asset_card(asset, immich_url) for asset in assets)
    album_options = render_album_filter_options(albums)
    content_filter_options = render_content_filter_options(assets)
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
      select,
      button {{
        background: #10141c;
        border-color: #3c4658;
        color: #edf2fb;
      }}
      details,
      .score-row {{
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
      <button id="toggle-components" type="button">Show score components</button>
      <button id="toggle-inputs" type="button">Show scoring inputs</button>
      <button id="toggle-faces" type="button">Show face boxes</button>
    </div>
    <section class="grid">
      {cards}
    </section>
  </main>
  <script>
    const prefix = "immich-highlights-review:";
    const faceToggleKey = prefix + "show-face-overlays";
    const albumFilterKey = prefix + "album-filter";
    const contentFilterKey = prefix + "content-filter";
    const componentsToggleKey = prefix + "show-score-components";
    const inputsToggleKey = prefix + "show-scoring-inputs";
    const faceButton = document.querySelector("#toggle-faces");
    const componentsButton = document.querySelector("#toggle-components");
    const inputsButton = document.querySelector("#toggle-inputs");
    const albumFilter = document.querySelector("#album-filter");
    const contentFilter = document.querySelector("#content-filter");
    const visibleCount = document.querySelector("#visible-count");

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

    function applyFilters() {{
      const selectedAlbum = albumFilter.value || "all";
      const selectedContentFilter = contentFilter.value || "all";
      localStorage.setItem(albumFilterKey, selectedAlbum);
      localStorage.setItem(contentFilterKey, selectedContentFilter);
      let count = 0;
      for (const card of document.querySelectorAll(".card")) {{
        const visible = cardMatchesAlbum(card, selectedAlbum)
          && cardMatchesContentFilter(card, selectedContentFilter);
        card.classList.toggle("hidden", !visible);
        if (visible) {{
          count += 1;
        }}
      }}
      visibleCount.textContent = String(count);
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
    albumFilter.addEventListener("change", applyFilters);
    contentFilter.addEventListener("change", applyFilters);
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
    attach_album_memberships(assets, memberships)
    if download_thumbnails:
        attach_local_thumbnails(
            assets,
            immich_url,
            api_key,
            output_path,
        )
    html_text = render_review_html(assets, immich_url, albums=albums)
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
