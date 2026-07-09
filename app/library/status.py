"""
Shared logic for keeping a LibraryItem's status lined up with how many
episodes of a TV show the signed-in user has actually watched.

Two entry points:
  - sync_show_completion: single show, used right after an episode
    checkin is toggled on show_detail / the watchlist banner.
  - sync_shows_completion_bulk: many shows at once, used right after a
    TV Time import so we don't hit TMDB (or the DB) once per show.

Both funnel into apply_status_from_counts, which is the one place the
actual status rules live:
  - a show the user explicitly dropped is never touched
  - fully watched (and TMDB actually reports episodes) -> "completed"
  - zero episodes watched -> "plan_to_watch" (this is what powers the
    "Interested" page -- shows land there until the first checkin)
  - anything in between -> "watching"
"""
import logging

from app.extensions import db
from app.models import LibraryItem, EpisodeCheckIn
from app.services import tmdb_cache

logger = logging.getLogger(__name__)


def _total_regular_episodes(details):
    """Sums episode_count across a show's regular seasons (season 0 /
    specials are already excluded by tmdb.get_show_details)."""
    return sum(s.get("episode_count", 0) for s in (details or {}).get("seasons", []))


def apply_status_from_counts(item, total_episodes, watched_count):
    """Pure function (no DB/network access) -- mutates item.status in place."""
    if item.status == "dropped":
        return

    if total_episodes > 0 and watched_count >= total_episodes:
        item.status = "completed"
    elif watched_count == 0:
        item.status = "plan_to_watch"
    elif item.status in ("completed", "plan_to_watch"):
        item.status = "watching"


def sync_show_completion(user_id, tmdb_id):
    """Single-show version. Call this after adding/removing a checkin."""
    item = LibraryItem.query.filter_by(
        user_id=user_id, tmdb_id=tmdb_id, media_type="tv"
    ).first()
    if not item:
        return None

    try:
        details = tmdb_cache.get_show_details_cached(tmdb_id)
    except Exception:
        logger.warning("sync_show_completion: TMDB fetch failed for tmdb_id=%s", tmdb_id, exc_info=True)
        return item  # TMDB unreachable right now -- leave status as-is

    total = _total_regular_episodes(details)
    watched_count = EpisodeCheckIn.query.filter(
        EpisodeCheckIn.user_id == user_id,
        EpisodeCheckIn.tmdb_show_id == tmdb_id,
        EpisodeCheckIn.season_number > 0,
    ).count()

    before = item.status
    apply_status_from_counts(item, total, watched_count)
    logger.info(
        "sync_show_completion: tmdb_id=%s total_episodes=%s watched=%s status %s -> %s",
        tmdb_id, total, watched_count, before, item.status,
    )
    return item


def sync_shows_completion_bulk(user_id, tmdb_ids, items_by_tmdb_id):
    """
    Bulk version, used right after a TV Time import.
    items_by_tmdb_id: {tmdb_id: LibraryItem} for the shows touched by the
    import (media_type == "tv" only -- movies have no episode checkins).
    """
    tmdb_ids = [t for t in dict.fromkeys(tmdb_ids) if t in items_by_tmdb_id]
    if not tmdb_ids:
        return

    details_map = tmdb_cache.get_shows_details_bulk(tmdb_ids)

    missing = [t for t in tmdb_ids if t not in details_map]
    if missing:
        logger.warning(
            "sync_shows_completion_bulk: TMDB details missing for %d/%d shows: %s",
            len(missing), len(tmdb_ids), missing[:20],
        )

    watched_counts = dict(
        db.session.query(EpisodeCheckIn.tmdb_show_id, db.func.count(EpisodeCheckIn.id))
        .filter(
            EpisodeCheckIn.user_id == user_id,
            EpisodeCheckIn.tmdb_show_id.in_(tmdb_ids),
            EpisodeCheckIn.season_number > 0,
        )
        .group_by(EpisodeCheckIn.tmdb_show_id)
        .all()
    )

    completed_count = 0
    for tid in tmdb_ids:
        item = items_by_tmdb_id[tid]
        details = details_map.get(tid)
        if details is None:
            continue  # TMDB fetch failed for this one -- leave status as-is
        total = _total_regular_episodes(details)
        watched = watched_counts.get(tid, 0)
        apply_status_from_counts(item, total, watched)
        if item.status == "completed":
            completed_count += 1
        elif total > 0 and watched >= total - 2 and item.status != "completed":
            # Close-but-not-quite is the most common cause of "why isn't
            # this marked complete" -- log it so a season-numbering /
            # unaired-episode mismatch is easy to spot.
            logger.info(
                "sync_shows_completion_bulk: near-complete but not: tmdb_id=%s "
                "total_episodes=%s watched=%s status=%s",
                tid, total, watched, item.status,
            )

    logger.info(
        "sync_shows_completion_bulk: processed %d shows, %d marked completed, %d TMDB fetch failures",
        len(tmdb_ids), completed_count, len(missing),
    )