import requests
from flask import current_app

POSTER_BASE = "https://image.tmdb.org/t/p/w342"

# Every outbound TMDB call gets this timeout. Without one, requests will
# happily hang forever on a slow/stalled connection -- and since the TV Time
# import calls find_tv_by_tvdb_id once per show synchronously, a single hung
# connection would tie up the whole import request (and its worker)
# indefinitely.
REQUEST_TIMEOUT = 8  # seconds


def _headers():
    return {
        "Authorization": f"Bearer {current_app.config['TMDB_ACCESS_TOKEN']}",
        "accept": "application/json",
    }


def search_shows(query):
    """
    Searches movies + TV in one call. Returns a normalized list:
    [{tmdb_id, media_type, title, year, poster_url, overview}, ...]
    """
    if not query:
        return []

    url = f"{current_app.config['TMDB_BASE_URL']}/search/multi"
    resp = requests.get(
        url,
        headers=_headers(),
        params={"query": query, "include_adult": "false"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    normalized = []
    for r in results:
        media_type = r.get("media_type")
        if media_type not in ("movie", "tv"):
            continue  # skip "person" results etc.

        title = r.get("title") or r.get("name")
        date = r.get("release_date") or r.get("first_air_date") or ""
        poster_path = r.get("poster_path")

        normalized.append({
            "tmdb_id": r["id"],
            "media_type": media_type,
            "title": title,
            "year": date[:4] if date else "N/A",
            "poster_url": f"{POSTER_BASE}{poster_path}" if poster_path else None,
            "overview": r.get("overview", ""),
        })
    return normalized


def get_show_details(media_type, tmdb_id):
    """
    Fetches full details for a single movie/tv show for the detail page.
    For TV shows, includes the season list (season_number, episode_count).
    """
    url = f"{current_app.config['TMDB_BASE_URL']}/{media_type}/{tmdb_id}"
    resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    title = data.get("title") or data.get("name")
    date = data.get("release_date") or data.get("first_air_date") or ""
    poster_path = data.get("poster_path")

    result = {
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "title": title,
        "year": date[:4] if date else "N/A",
        "poster_url": f"{POSTER_BASE}{poster_path}" if poster_path else None,
        "overview": data.get("overview", ""),
        "genres": [g["name"] for g in data.get("genres", [])],
    }

    if media_type == "tv":
        # Skip "season 0" (specials) in the normal season list
        result["seasons"] = [
            {
                "season_number": s["season_number"],
                "name": s.get("name"),
                "episode_count": s.get("episode_count", 0),
                "air_date": s.get("air_date"),
            }
            for s in data.get("seasons", [])
            if s["season_number"] > 0
        ]

    return result


def find_tv_by_tvdb_id(tvdb_id):
    """
    Converts a TVDB show ID (what TV Time exports use) into TMDB TV data,
    via TMDB's /find endpoint. Returns {tmdb_id, title, poster_url} or None
    if there's no match. The /find response already includes poster_path,
    so this avoids a second API call just to get the poster.
    """
    url = f"{current_app.config['TMDB_BASE_URL']}/find/{tvdb_id}"
    resp = requests.get(
        url,
        headers=_headers(),
        params={"external_source": "tvdb_id"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    results = resp.json().get("tv_results", [])
    if not results:
        return None

    r = results[0]
    poster_path = r.get("poster_path")
    return {
        "tmdb_id": r["id"],
        "title": r.get("name"),
        "poster_url": f"{POSTER_BASE}{poster_path}" if poster_path else None,
    }


def get_season_episodes(tv_id, season_number):
    """
    Fetches the episode list for one season of a TV show.
    Returns [{episode_number, name, air_date, overview, still_url}, ...]
    """
    url = f"{current_app.config['TMDB_BASE_URL']}/tv/{tv_id}/season/{season_number}"
    resp = requests.get(url, headers=_headers(), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    episodes = []
    for ep in data.get("episodes", []):
        still_path = ep.get("still_path")
        episodes.append({
            "episode_number": ep["episode_number"],
            "name": ep.get("name"),
            "air_date": ep.get("air_date"),
            "overview": ep.get("overview", ""),
            "still_url": f"{POSTER_BASE}{still_path}" if still_path else None,
        })
    return episodes