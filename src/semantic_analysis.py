"""Semantic/user metadata analysis, face facts, and content-filter matching."""

from datetime import timedelta
import logging

from PIL import Image

try:
    from .asset_analysis import (
        compute_best_face_quality,
        get_asset_exif,
        get_exif_exposure_seconds,
        get_exif_iso,
        has_location,
        is_edited,
        is_favorite,
        normalize_immich_faces,
        normalize_rating,
    )
    from .asset_discovery import get_asset_id
    from .scoring_engine import DEFAULT_SCORING_CONFIG
except ImportError:
    from asset_analysis import (
        compute_best_face_quality,
        get_asset_exif,
        get_exif_exposure_seconds,
        get_exif_iso,
        has_location,
        is_edited,
        is_favorite,
        normalize_immich_faces,
        normalize_rating,
    )
    from asset_discovery import get_asset_id
    from scoring_engine import DEFAULT_SCORING_CONFIG


CONTENT_FILTER_MAX_CONTEXT_DAYS = 365
logger = logging.getLogger("semantic_analysis")


def _load_yolo_model():
    """Load the YOLO model once and reuse it across images for efficiency."""
    try:
        from ultralytics import YOLO
    except ImportError:
        return None

    try:
        return YOLO("yolov8n.pt")
    except Exception as exc:
        logger.warning("Unable to load YOLO model: %s", exc)
        return None


def detect_people_with_yolo(
    pil_image,
    max_size: int = 640,
    confidence_threshold: float = 0.3,
) -> list[dict]:
    """Use a lightweight YOLO model to detect people on CPU-only systems."""
    if pil_image is None:
        return []

    try:
        import numpy as np
    except ImportError:
        return []

    try:
        image = pil_image.convert("RGB")
        original_width, original_height = image.size
        scale = min(1.0, max_size / max(original_width, original_height))
        if scale < 1.0:
            resized = image.resize(
                (
                    max(1, int(original_width * scale)),
                    max(1, int(original_height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )
        else:
            resized = image

        model = _load_yolo_model()
        if model is None:
            logger.warning("YOLO detector is unavailable; no labels will be produced")
            return []

        results = model(
            np.array(resized),
            imgsz=max_size,
            stream=False,
            conf=confidence_threshold,
            device="cpu",
            max_det=50,
        )
        detections = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                label = result.names[int(box.cls[0])]
                confidence = float(box.conf[0])
                detections.append(
                    {
                        "label": label,
                        "confidence": confidence,
                        "x": int(x1 / scale) if scale < 1.0 else int(x1),
                        "y": int(y1 / scale) if scale < 1.0 else int(y1),
                        "width": (
                            int((x2 - x1) / scale) if scale < 1.0 else int(x2 - x1)
                        ),
                        "height": (
                            int((y2 - y1) / scale) if scale < 1.0 else int(y2 - y1)
                        ),
                    }
                )
        if not detections:
            logger.debug("YOLO produced no detections for this image")
        return detections
    except Exception as exc:
        logger.warning("YOLO detection failed: %s", exc)
        return []


def get_asset_exif_for_storage(meta: dict) -> dict:
    """Return EXIF plus useful asset-level datetime fields for review exports."""
    exif = dict(get_asset_exif(meta) if isinstance(meta, dict) else {})
    for key in ("localDateTime", "fileCreatedAt", "createdAt"):
        if key in meta and key not in exif:
            exif[key] = meta[key]
    return exif


def analyze_semantic_metadata(
    asset_meta: dict,
    pil_image,
    immich_faces: list[dict] | None = None,
    content_filter_matches: list[dict] | None = None,
    content_filter_penalty: int = 0,
    person_detector=None,
    confidence_threshold: float = 0.3,
) -> dict:
    """Collect semantic/user facts from Immich metadata and face boxes."""
    details = {}
    try:
        details["faces"] = normalize_immich_faces(immich_faces or [], pil_image.size)
        details["face_count"] = len(details["faces"])
        details["face_quality"] = compute_best_face_quality(pil_image, details["faces"])
    except Exception:
        details["faces"] = []
        details["face_count"] = 0
        details["face_quality"] = 0

    if person_detector is None:
        person_detector = detect_people_with_yolo
    try:
        try:
            yolo_detections = person_detector(
                pil_image,
                confidence_threshold=confidence_threshold,
            )
        except TypeError:
            yolo_detections = person_detector(pil_image)
        details["yolo_detections"] = yolo_detections or []
        details["yolo_labels"] = [
            detection.get("label")
            for detection in details["yolo_detections"]
            if detection.get("label")
        ]
        if not details["yolo_labels"]:
            logger.debug("YOLO labels were empty for this asset")
        person_detections = [
            detection
            for detection in details["yolo_detections"]
            if detection.get("label") in {None, "person"}
        ]
        details["person_detections"] = person_detections
        details["person_count"] = len(details["person_detections"])
        details["person_present"] = details["person_count"] > 0
    except Exception:
        details["yolo_detections"] = []
        details["yolo_labels"] = []
        details["person_detections"] = []
        details["person_count"] = 0
        details["person_present"] = False

    details["exif"] = get_asset_exif(asset_meta)
    details["rating"] = normalize_rating(details["exif"].get("rating"))
    details["iso"] = get_exif_iso(details["exif"])
    details["exposure_seconds"] = get_exif_exposure_seconds(details["exif"])
    details["has_location"] = has_location(details["exif"])
    details["is_favorite"] = is_favorite(asset_meta)
    details["is_edited"] = is_edited(asset_meta)
    details["content_filter_matches"] = content_filter_matches or []
    details["content_labels"] = [
        match["label"] for match in details["content_filter_matches"]
    ]
    details["content_filter_penalty"] = content_filter_penalty
    return details


def cached_content_filter_state(cached: dict) -> tuple[list[str], int]:
    """Read stored content-filter state from cached score details."""
    inputs = cached.get("score_details", {}).get("inputs", {})
    return (
        inputs.get("content_labels", []),
        inputs.get("content_filter_penalty", 0),
    )


def content_filter_state(
    matches: list[dict],
    scoring_config=DEFAULT_SCORING_CONFIG,
) -> tuple[list[str], int]:
    """Return labels and the penalty from the strongest smart-search match."""
    labels = [match["label"] for match in matches]
    if not matches:
        return labels, 0

    # A photo can appear in multiple smart-search filters. Stacking every
    # penalty overreacts to similar queries, so the score uses only the filter
    # where Immich ranked the photo highest. Rank 1 is strongest.
    strongest_match = min(matches, key=lambda match: match.get("rank", 999999))
    return labels, max(
        scoring_config.content_filter_min_penalty,
        strongest_match["penalty"],
    )


def content_filter_search_windows(rule):
    """Yield widening context windows used to make smart-search filters reliable."""
    # Immich smart search returns ranked results, not confidence scores. If we
    # ask for the "best" screenshot-like photos inside a tiny album window, the
    # last returned items may simply be the least-bad matches. Widening the
    # context gives Immich enough photos to make the top results meaningful.
    album_days = max(1, (rule.taken_before - rule.taken_after).days)
    days = album_days
    yielded = set()
    while True:
        window_days = max(album_days, min(days, CONTENT_FILTER_MAX_CONTEXT_DAYS))
        if window_days not in yielded:
            yielded.add(window_days)
            yield rule.taken_before - timedelta(days=window_days), rule.taken_before
        if window_days >= CONTENT_FILTER_MAX_CONTEXT_DAYS:
            return
        days *= 2


def content_filter_context_window(client, rule, content_filter, log=logger):
    """Find the smallest widened window with enough photos for smart search."""
    last_window = None
    last_count = 0
    for taken_after, taken_before in content_filter_search_windows(rule):
        count = client.count_assets(
            taken_after=taken_after.isoformat(),
            taken_before=taken_before.isoformat(),
        )
        log.debug(
            "Content filter '%s' context window for album '%s': "
            "takenAfter=%s, takenBefore=%s, pool=%s, required=%s",
            content_filter.label,
            rule.name,
            taken_after.isoformat(),
            taken_before.isoformat(),
            count,
            content_filter.min_search_pool,
        )
        last_window = (taken_after, taken_before)
        last_count = count
        if count >= content_filter.min_search_pool:
            return taken_after, taken_before, count
    return (*last_window, last_count) if last_window else (None, None, 0)


def collect_content_filter_matches(
    client,
    rule,
    content_filters,
    candidate_asset_ids: set[str],
    log=logger,
) -> dict[str, list[dict]]:
    """Run configured smart searches and index filter matches by asset id."""
    matches_by_asset_id = {}
    for content_filter in content_filters:
        taken_after, taken_before, pool_count = content_filter_context_window(
            client,
            rule,
            content_filter,
            log,
        )
        if pool_count < content_filter.min_search_pool:
            log.info(
                "Skipping content filter '%s' for album '%s': "
                "largest context pool has %s assets, required=%s",
                content_filter.label,
                rule.name,
                pool_count,
                content_filter.min_search_pool,
            )
            continue

        log.info(
            "Running content filter '%s' with smart search query=%r for album '%s' "
            "against context pool of %s assets",
            content_filter.label,
            content_filter.query,
            rule.name,
            pool_count,
        )
        filter_match_count = 0
        filter_overlap_count = 0
        for rank, asset in enumerate(
            client.iter_smart_search_assets(
                query=content_filter.query,
                page_size=min(content_filter.max_results, 1000),
                max_assets=content_filter.max_results,
                taken_after=taken_after.isoformat(),
                taken_before=taken_before.isoformat(),
            ),
            start=1,
        ):
            asset_id = get_asset_id(asset)
            if not asset_id:
                continue
            filter_match_count += 1
            if asset_id not in candidate_asset_ids:
                continue
            filter_overlap_count += 1
            log.debug(
                "Content filter '%s' rank=%s matched asset %s for album '%s'",
                content_filter.label,
                rank,
                asset_id,
                rule.name,
            )
            matches_by_asset_id.setdefault(asset_id, []).append(
                {
                    "label": content_filter.label,
                    "query": content_filter.query,
                    "penalty": content_filter.penalty,
                    "rank": rank,
                }
            )
        log.info(
            "Content filter '%s' returned %s ranked assets and matched %s "
            "album candidates for album '%s'",
            content_filter.label,
            filter_match_count,
            filter_overlap_count,
            rule.name,
        )
    log.info(
        "Content filters matched %s unique assets for album '%s'",
        len(matches_by_asset_id),
        rule.name,
    )
    return matches_by_asset_id
