import argparse
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


def load_processed_assets(db_path: str, limit: int | None = None) -> list[dict]:
    """Read scored assets from the database in descending score order."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    sql = (
        "SELECT asset_id, score, rating, score_details_json, processed_at "
        "FROM processed_assets ORDER BY score DESC, processed_at DESC"
    )
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "asset_id": row[0],
            "score": row[1],
            "rating": row[2],
            "score_details": parse_json(row[3], {}),
            "processed_at": row[4],
        }
        for row in rows
    ]


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


def render_asset_card(asset: dict, immich_url: str) -> str:
    """Render one scored asset review card."""
    asset_id = asset["asset_id"]
    score_details = asset.get("score_details", {})
    components = score_details.get("components", {})
    inputs = score_details.get("inputs", {})
    link = immich_asset_url(immich_url, asset_id)
    thumbnail = asset.get("thumbnail_src") or immich_thumbnail_url(immich_url, asset_id)
    escaped_id = html.escape(asset_id)
    escaped_link = html.escape(link, quote=True)
    escaped_thumbnail = html.escape(thumbnail, quote=True)
    return f"""
    <article class="card" data-asset-id="{escaped_id}">
      <a class="thumb-link" href="{escaped_link}" target="_blank" rel="noreferrer">
        <img
          class="thumbnail"
          src="{escaped_thumbnail}"
          alt="Thumbnail for {escaped_id}"
          loading="lazy"
        >
      </a>
      <header>
        <div>
          <a class="asset-link" href="{escaped_link}" target="_blank" rel="noreferrer">
            Open in Immich
          </a>
          <div class="asset-id">{escaped_id}</div>
        </div>
        <div class="score">{html.escape(str(asset["score"]))}</div>
      </header>

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
        <label class="notes">
          Notes
          <textarea
            data-field="notes"
            rows="2"
            placeholder="Why does this match or miss?"
          ></textarea>
        </label>
      </section>

      <details open>
        <summary>Score components</summary>
        {render_score_items(components)}
      </details>
      <details>
        <summary>Scoring inputs</summary>
        {render_score_items(inputs)}
      </details>
    </article>
    """


def render_review_html(assets: list[dict], immich_url: str) -> str:
    """Render a full static review page."""
    cards = "\n".join(render_asset_card(asset, immich_url) for asset in assets)
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
    .thumb-link {{
      display: block;
      margin: -4px -4px 14px;
      overflow: hidden;
      border-radius: 6px;
      background: #e8edf5;
    }}
    .thumbnail {{
      display: block;
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: cover;
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
    .asset-id {{
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
    select,
    textarea {{
      box-sizing: border-box;
      width: 100%;
      border: 1px solid #cbd3df;
      border-radius: 6px;
      padding: 8px;
      font: inherit;
    }}
    textarea {{
      resize: vertical;
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
      .asset-id,
      label,
      .score-row span {{
        color: #a8b3c6;
      }}
      select,
      textarea {{
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
      {len(assets)} scored assets. Labels are stored only in this browser's
      local storage. Use the Immich links to inspect photos, then compare your
      judgement with the score components.
    </p>
    <section class="grid">
      {cards}
    </section>
  </main>
  <script>
    const prefix = "immich-highlights-review:";
    for (const card of document.querySelectorAll(".card")) {{
      const assetId = card.dataset.assetId;
      for (const field of card.querySelectorAll("[data-field]")) {{
        const key = prefix + assetId + ":" + field.dataset.field;
        field.value = localStorage.getItem(key) || "";
        field.addEventListener("input", () => {{
          localStorage.setItem(key, field.value);
        }});
      }}
    }}
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
    if download_thumbnails:
        attach_local_thumbnails(
            assets,
            immich_url,
            api_key,
            output_path,
        )
    html_text = render_review_html(assets, immich_url)
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
