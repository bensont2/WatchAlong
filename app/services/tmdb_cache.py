import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import current_app

from app.extensions import db
from app.models import ShowMetaCache, SeasonMetaCache
from app.services import tmdb

CACHE_TTL = timedelta(hours=12)
MAX_WORKERS = 10  # concurrent TMDB requests when refreshing a cold/stale cache


def _fetch_show_in_thread(app, tmdb_id):
    """
    Flask's current_app is thread-local and does NOT automatically exist
    inside threads spawned by ThreadPoolExecutor -- calling tmdb.* directly
    from a worker thread raises "Working outside of application context".
    Each worker has to push its own app context first.
    """
    with app.app_context():
        return tmdb.get_show_details("tv", tmdb_id)


def _fetch_season_in_thread(app, tmdb_id, season_number):
    with app.app_context():
        return tmdb.get_season_episodes(tmdb_id, season_number)


def get_show_details_cached(tmdb_id):
    """
    Single-item version, used by pages that only ever need one show
    (show_detail, episode_detail). For pages that loop over many shows
    (Watchlist), use get_shows_details_bulk instead -- see below.
    """
    cached = ShowMetaCache.query.get(tmdb_id)
    if cached and (datetime.utcnow() - cached.fetched_at) < CACHE_TTL:
        return {
            "tmdb_id": tmdb_id,
            "title": cached.title,
            "poster_url": cached.poster_url,
            "seasons": json.loads(cached.seasons_json),
        }

    details = tmdb.get_show_details("tv", tmdb_id)
    _write_show_cache(tmdb_id, details, cached)
    db.session.commit()
    return details


def get_season_episodes_cached(tmdb_id, season_number):
    """Single-item version, used by show_detail/episode_detail pages."""
    cached = SeasonMetaCache.query.get((tmdb_id, season_number))
    if cached and (datetime.utcnow() - cached.fetched_at) < CACHE_TTL:
        return json.loads(cached.episodes_json)

    episodes = tmdb.get_season_episodes(tmdb_id, season_number)
    _write_season_cache(tmdb_id, season_number, episodes, cached)
    db.session.commit()
    return episodes


def get_shows_details_bulk(tmdb_ids):
    """
    Returns {tmdb_id: details_dict} for every id given. Cache hits are read
    with a single query; cache misses/stale entries are fetched from TMDB
    concurrently (network latency is the real bottleneck, so fetching
    N shows in parallel instead of one-by-one is what actually fixes a
    slow first-load / post-cache-expiry Watchlist visit). All writes are
    batched into one commit at the end.
    """
    tmdb_ids = list(dict.fromkeys(tmdb_ids))  # dedupe, keep order
    if not tmdb_ids:
        return {}

    now = datetime.utcnow()
    cached_rows = {
        row.tmdb_id: row
        for row in ShowMetaCache.query.filter(ShowMetaCache.tmdb_id.in_(tmdb_ids)).all()
    }

    results = {}
    to_fetch = []
    for tid in tmdb_ids:
        row = cached_rows.get(tid)
        if row and (now - row.fetched_at) < CACHE_TTL:
            results[tid] = {
                "tmdb_id": tid,
                "title": row.title,
                "poster_url": row.poster_url,
                "seasons": json.loads(row.seasons_json),
            }
        else:
            to_fetch.append(tid)

    if to_fetch:
        real_app = current_app._get_current_object()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_to_id = {
                pool.submit(_fetch_show_in_thread, real_app, tid): tid for tid in to_fetch
            }
            for future in as_completed(future_to_id):
                tid = future_to_id[future]
                try:
                    details = future.result()
                except Exception:
                    continue  # TMDB couldn't return this one -- skip it
                _write_show_cache(tid, details, cached_rows.get(tid))
                results[tid] = details

        db.session.commit()

    return results


def get_seasons_bulk(season_keys):
    """
    season_keys: iterable of (tmdb_id, season_number) tuples.
    Returns {(tmdb_id, season_number): episodes_list}, same caching +
    parallel-fetch approach as get_shows_details_bulk.
    """
    season_keys = list(dict.fromkeys(season_keys))
    if not season_keys:
        return {}

    now = datetime.utcnow()
    tmdb_ids = list({k[0] for k in season_keys})
    cached_rows = {
        (row.tmdb_id, row.season_number): row
        for row in SeasonMetaCache.query.filter(SeasonMetaCache.tmdb_id.in_(tmdb_ids)).all()
    }

    results = {}
    to_fetch = []
    for key in season_keys:
        row = cached_rows.get(key)
        if row and (now - row.fetched_at) < CACHE_TTL:
            results[key] = json.loads(row.episodes_json)
        else:
            to_fetch.append(key)

    if to_fetch:
        real_app = current_app._get_current_object()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_to_key = {
                pool.submit(_fetch_season_in_thread, real_app, tid, snum): (tid, snum)
                for tid, snum in to_fetch
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    episodes = future.result()
                except Exception:
                    continue
                _write_season_cache(key[0], key[1], episodes, cached_rows.get(key))
                results[key] = episodes

        db.session.commit()

    return results


def _write_show_cache(tmdb_id, details, existing_row):
    if existing_row:
        existing_row.title = details.get("title")
        existing_row.poster_url = details.get("poster_url")
        existing_row.seasons_json = json.dumps(details.get("seasons", []))
        existing_row.fetched_at = datetime.utcnow()
    else:
        db.session.add(ShowMetaCache(
            tmdb_id=tmdb_id,
            title=details.get("title"),
            poster_url=details.get("poster_url"),
            seasons_json=json.dumps(details.get("seasons", [])),
            fetched_at=datetime.utcnow(),
        ))


def _write_season_cache(tmdb_id, season_number, episodes, existing_row):
    if existing_row:
        existing_row.episodes_json = json.dumps(episodes)
        existing_row.fetched_at = datetime.utcnow()
    else:
        db.session.add(SeasonMetaCache(
            tmdb_id=tmdb_id,
            season_number=season_number,
            episodes_json=json.dumps(episodes),
            fetched_at=datetime.utcnow(),
        ))