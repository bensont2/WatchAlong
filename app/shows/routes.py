from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user

from app.shows import shows_bp
from app.extensions import db
from app.models import LibraryItem, EpisodeCheckIn, User
from app.services import tmdb
from app.services import tmdb_cache


@shows_bp.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()
    results = tmdb.search_shows(query) if query else []
    return render_template("search.html", query=query, results=results)


@shows_bp.route("/search/suggest")
@login_required
def search_suggest():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify([])
    results = tmdb.search_shows(query)[:6]
    return jsonify(results)


def _next_episode_for(user_id, tv_id, seasons):
    """
    Single-show version: queries this one show's check-ins, then delegates
    to _compute_next_episode. Fine for single-show pages (show_detail),
    but pages that loop over many shows (Watchlist) should fetch all
    check-ins once up front and call _compute_next_episode directly --
    see watchlist() below -- to avoid one DB round trip per show.
    """
    watched = {
        (c.season_number, c.episode_number)
        for c in EpisodeCheckIn.query.filter_by(user_id=user_id, tmdb_show_id=tv_id).all()
    }
    return _compute_next_episode(watched, seasons)

def _compute_next_episode(watched_set, seasons):
    """
    Pure function, no DB access: given a set of (season_number,
    episode_number) tuples already watched, and a show's season list,
    returns the first unwatched {season_number, episode_number}, or None.
    """
    for season in seasons:
        season_num = season["season_number"]
        for ep_num in range(1, season["episode_count"] + 1):
            if (season_num, ep_num) not in watched_set:
                return {"season_number": season_num, "episode_number": ep_num}
    return None



@shows_bp.route("/show/<media_type>/<int:tmdb_id>")
@login_required
def show_detail(media_type, tmdb_id):
    details = tmdb.get_show_details(media_type, tmdb_id)

    my_item = LibraryItem.query.filter_by(
        user_id=current_user.id, tmdb_id=tmdb_id, media_type=media_type
    ).first()

    public_items = (
        db.session.query(LibraryItem, User.username)
        .join(User, LibraryItem.user_id == User.id)
        .filter(
            LibraryItem.tmdb_id == tmdb_id,
            LibraryItem.media_type == media_type,
            LibraryItem.is_public.is_(True),
        )
        .order_by(
            (LibraryItem.user_id == current_user.id).desc(),
            LibraryItem.updated_at.desc(),
        )
        .all()
    )

    episodes = None
    selected_season = None
    watched_set = set()
    comments_by_episode = {}
    next_episode = None

    if media_type == "tv" and details.get("seasons"):
        selected_season = request.args.get("season", type=int) or details["seasons"][0]["season_number"]
        episodes = tmdb.get_season_episodes(tmdb_id, selected_season)

        season_checkins = EpisodeCheckIn.query.filter_by(
            user_id=current_user.id, tmdb_show_id=tmdb_id, season_number=selected_season
        ).all()
        watched_set = {c.episode_number for c in season_checkins}
        comments_by_episode = {
            c.episode_number: {"comment": c.comment, "is_public": c.is_public}
            for c in season_checkins
            if c.comment
        }

        next_episode = _next_episode_for(current_user.id, tmdb_id, details["seasons"])

    return render_template(
        "show_detail.html",
        show=details,
        my_item=my_item,
        public_items=public_items,
        episodes=episodes,
        selected_season=selected_season,
        watched_set=watched_set,
        comments_by_episode=comments_by_episode,
        next_episode=next_episode,
    )


@shows_bp.route("/show/tv/<int:tmdb_id>/episode/checkin", methods=["POST"])
@login_required
def toggle_episode(tmdb_id):
    """
    AJAX endpoint. Toggles a single episode watched/unwatched.
    Body (JSON): {season_number, episode_number, episode_name, show_title}
    """
    payload = request.get_json(silent=True) or {}
    season_number = payload.get("season_number")
    episode_number = payload.get("episode_number")

    if season_number is None or episode_number is None:
        return jsonify({"error": "season_number and episode_number are required"}), 400

    existing = EpisodeCheckIn.query.filter_by(
        user_id=current_user.id,
        tmdb_show_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()

    library_item = LibraryItem.query.filter_by(
        user_id=current_user.id, tmdb_id=tmdb_id, media_type="tv"
    ).first()
    is_public = library_item.is_public if library_item else False

    if existing:
        db.session.delete(existing)
        db.session.commit()
        return jsonify({"watched": False})

    checkin = EpisodeCheckIn(
        user_id=current_user.id,
        tmdb_show_id=tmdb_id,
        show_title=payload.get("show_title", ""),
        season_number=season_number,
        episode_number=episode_number,
        episode_name=payload.get("episode_name"),
        is_public=is_public,
    )
    db.session.add(checkin)

    if not library_item:
        library_item = LibraryItem(
            user_id=current_user.id,
            tmdb_id=tmdb_id,
            media_type="tv",
            title=payload.get("show_title", ""),
            poster_path=payload.get("poster_path"),
            status="watching",
        )
        db.session.add(library_item)

    db.session.commit()
    return jsonify({"watched": True})


@shows_bp.route("/show/tv/<int:tmdb_id>/episode/comment", methods=["POST"])
@login_required
def comment_episode(tmdb_id):
    """
    AJAX endpoint used by the inline comment box on the show detail page.
    Saves/updates a comment on a specific episode, along with its own
    public/private visibility. The episode must already be checked in.
    Body (JSON): {season_number, episode_number, comment, is_public}
    """
    payload = request.get_json(silent=True) or {}
    season_number = payload.get("season_number")
    episode_number = payload.get("episode_number")
    comment_text = (payload.get("comment") or "").strip()
    is_public = bool(payload.get("is_public"))

    if season_number is None or episode_number is None:
        return jsonify({"error": "season_number and episode_number are required"}), 400

    checkin = EpisodeCheckIn.query.filter_by(
        user_id=current_user.id,
        tmdb_show_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()

    if not checkin:
        return jsonify({"error": "Mark the episode watched before commenting on it."}), 400

    checkin.comment = comment_text or None
    checkin.is_public = is_public
    db.session.commit()
    return jsonify({"comment": checkin.comment, "is_public": checkin.is_public})


@shows_bp.route("/show/<media_type>/<int:tmdb_id>/status", methods=["POST"])
@login_required
def set_status(media_type, tmdb_id):
    """Quick status change: watching / completed / plan_to_watch / dropped."""
    status = request.form.get("status")
    title = request.form.get("title")
    poster_path = request.form.get("poster_path")

    valid_statuses = {"watching", "completed", "plan_to_watch", "dropped"}
    if status not in valid_statuses:
        flash("Invalid status.", "error")
        return redirect(url_for("shows.show_detail", media_type=media_type, tmdb_id=tmdb_id))

    item = LibraryItem.query.filter_by(
        user_id=current_user.id, tmdb_id=tmdb_id, media_type=media_type
    ).first()

    if item:
        item.status = status
    else:
        item = LibraryItem(
            user_id=current_user.id,
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            poster_path=poster_path,
            status=status,
        )
        db.session.add(item)

    db.session.commit()
    flash("Status updated.", "success")
    return redirect(url_for("shows.show_detail", media_type=media_type, tmdb_id=tmdb_id))


@shows_bp.route("/show/<media_type>/<int:tmdb_id>/rate", methods=["POST"])
@login_required
def rate_show(media_type, tmdb_id):
    rating = request.form.get("rating", type=int)
    comment = request.form.get("comment", "").strip()
    is_public = request.form.get("visibility") == "public"
    title = request.form.get("title")
    poster_path = request.form.get("poster_path")

    if rating is not None and (rating < 1 or rating > 5):
        flash("Please select a rating between 1 and 5 stars.", "error")
        return redirect(url_for("shows.show_detail", media_type=media_type, tmdb_id=tmdb_id))

    item = LibraryItem.query.filter_by(
        user_id=current_user.id, tmdb_id=tmdb_id, media_type=media_type
    ).first()

    if item:
        item.rating = rating
        item.comment = comment or None
        item.is_public = is_public
    else:
        item = LibraryItem(
            user_id=current_user.id,
            tmdb_id=tmdb_id,
            media_type=media_type,
            title=title,
            poster_path=poster_path,
            rating=rating,
            comment=comment or None,
            is_public=is_public,
            status="completed" if rating else "watching",
        )
        db.session.add(item)

    # Note: episode-level visibility is controlled per-episode (see
    # comment_episode / save_episode_comment) and is no longer overwritten
    # by the show-level rating's visibility setting.

    db.session.commit()
    flash("Saved to your library.", "success")
    return redirect(url_for("shows.show_detail", media_type=media_type, tmdb_id=tmdb_id))


@shows_bp.route("/watchlist")
@login_required
def watchlist():
    """
    Shows the next unwatched episode for every TV show you're tracking
    (excluding dropped shows). Shows you haven't touched in 30+ days get
    bucketed separately so they don't get lost among active ones.

    Fetches show/season data in bulk with cache-misses resolved in
    parallel (see app/services/tmdb_cache.py) rather than looping over
    shows one at a time -- with a large tracked list (e.g. after a TV Time
    import), sequential fetching is the actual bottleneck, not just "cold
    cache vs warm cache".
    """
    tracked = (
        LibraryItem.query.filter_by(user_id=current_user.id, media_type="tv")
        .filter(LibraryItem.status != "dropped")
        .all()
    )

    now = datetime.utcnow()

    # Phase 1: get show details (seasons list + poster) for every tracked
    # show in one batch, fetching any cache misses concurrently.
    show_details_map = tmdb_cache.get_shows_details_bulk([item.tmdb_id for item in tracked])

    # Fetch every check-in for this user in ONE query and group by show.
    # Querying per-show here was the actual bottleneck on a remote DB like
    # Supabase: each query is a network round trip, so 250 tracked shows
    # meant 250+ round trips just for this, independent of the TMDB caching.
    tracked_ids = [item.tmdb_id for item in tracked]
    all_checkins = (
        EpisodeCheckIn.query.filter(
            EpisodeCheckIn.user_id == current_user.id,
            EpisodeCheckIn.tmdb_show_id.in_(tracked_ids),
        ).all()
        if tracked_ids
        else []
    )
    checkins_by_show = {}
    for c in all_checkins:
        checkins_by_show.setdefault(c.tmdb_show_id, []).append(c)

    # Phase 2: figure out each show's next unwatched episode. Pure
    # in-memory computation now -- no per-show DB query.
    next_episode_map = {}
    for item in tracked:
        details = show_details_map.get(item.tmdb_id)
        if not details:
            continue
        watched_set = {
            (c.season_number, c.episode_number) for c in checkins_by_show.get(item.tmdb_id, [])
        }
        next_ep = _compute_next_episode(watched_set, details.get("seasons", []))
        if next_ep:
            next_episode_map[item.tmdb_id] = next_ep

    # Phase 3: fetch episode name/still only for the specific seasons we
    # actually need (one per show with a next episode), again in one
    # batch with parallel cache-miss resolution.
    season_keys = [
        (tmdb_id, ep["season_number"]) for tmdb_id, ep in next_episode_map.items()
    ]
    season_map = tmdb_cache.get_seasons_bulk(season_keys)

    continuing = []
    stale = []

    for item in tracked:
        next_episode = next_episode_map.get(item.tmdb_id)
        if not next_episode:
            continue  # fully caught up, or TMDB couldn't return details

        details = show_details_map.get(item.tmdb_id, {})
        episodes = season_map.get((item.tmdb_id, next_episode["season_number"]), [])
        match = next(
            (e for e in episodes if e["episode_number"] == next_episode["episode_number"]),
            None,
        )
        episode_name = match.get("name") if match else None
        still_url = match.get("still_url") if match else None

        show_checkins = checkins_by_show.get(item.tmdb_id, [])
        last_checkin = max(show_checkins, key=lambda c: c.watched_at, default=None)
        last_activity_at = last_checkin.watched_at if last_checkin else item.added_at
        days_since = (now - last_activity_at).days

        entry = {
            "item": item,
            "next_episode": next_episode,
            "episode_name": episode_name,
            "still_url": still_url,
            "days_since": days_since,
            "poster_url": details.get("poster_url") or item.poster_path,
        }

        (stale if days_since >= 30 else continuing).append(entry)

    continuing.sort(key=lambda e: e["days_since"])
    stale.sort(key=lambda e: -e["days_since"])

    return render_template("watchlist.html", continuing=continuing, stale=stale)


@shows_bp.route("/show/tv/<int:tmdb_id>/season/<int:season_number>/episode/<int:episode_number>")
@login_required
def episode_detail(tmdb_id, season_number, episode_number):
    """
    Dedicated page for a single episode. This is where the "mark watched ->
    leave a comment" flow from the Watchlist banner lands.
    """
    details = tmdb.get_show_details("tv", tmdb_id)
    show_title = details.get("title", "")

    episodes = tmdb.get_season_episodes(tmdb_id, season_number)
    episode = next((e for e in episodes if e["episode_number"] == episode_number), None)
    if episode is None:
        flash("Episode not found.", "error")
        return redirect(url_for("shows.show_detail", media_type="tv", tmdb_id=tmdb_id, season=season_number))

    checkin = EpisodeCheckIn.query.filter_by(
        user_id=current_user.id,
        tmdb_show_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()

    # Public comments on this specific episode, from everyone (your own
    # pinned first, labeled "You")
    public_checkins = (
        db.session.query(EpisodeCheckIn, User.username)
        .join(User, EpisodeCheckIn.user_id == User.id)
        .filter(
            EpisodeCheckIn.tmdb_show_id == tmdb_id,
            EpisodeCheckIn.season_number == season_number,
            EpisodeCheckIn.episode_number == episode_number,
            EpisodeCheckIn.is_public.is_(True),
            EpisodeCheckIn.comment.isnot(None),
        )
        .order_by(
            (EpisodeCheckIn.user_id == current_user.id).desc(),
            EpisodeCheckIn.watched_at.desc(),
        )
        .all()
    )

    return render_template(
        "episode_detail.html",
        show_title=show_title,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
        episode=episode,
        checkin=checkin,
        public_checkins=public_checkins,
    )


@shows_bp.route(
    "/show/tv/<int:tmdb_id>/season/<int:season_number>/episode/<int:episode_number>/comment",
    methods=["POST"],
)
@login_required
def save_episode_comment(tmdb_id, season_number, episode_number):
    """
    Form-based save for the episode detail page's comment box (public or
    private). The episode must already be checked in.
    """
    comment_text = request.form.get("comment", "").strip()
    is_public = request.form.get("visibility") == "public"

    checkin = EpisodeCheckIn.query.filter_by(
        user_id=current_user.id,
        tmdb_show_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()

    if not checkin:
        flash("Mark the episode watched before commenting on it.", "error")
        return redirect(url_for(
            "shows.episode_detail", tmdb_id=tmdb_id,
            season_number=season_number, episode_number=episode_number,
        ))

    checkin.comment = comment_text or None
    checkin.is_public = is_public
    db.session.commit()
    flash("Comment saved.", "success")
    return redirect(url_for(
        "shows.episode_detail", tmdb_id=tmdb_id,
        season_number=season_number, episode_number=episode_number,
    ))