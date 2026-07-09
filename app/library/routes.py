import json
from datetime import datetime

from flask import render_template, redirect, url_for, flash, abort, request
from flask_login import login_required, current_user

from app.library import library_bp
from app.extensions import db
from app.models import LibraryItem, EpisodeCheckIn
from app.services import tmdb
from app.services.library_status import apply_status_from_counts

STATUS_MAP = {
    "continuing": "watching",
    "up_to_date": "watching",
    "not_started_yet": "plan_to_watch",
}

# Sanity caps on the uploaded export -- this endpoint only requires being
# logged in, so it shouldn't be able to make the server churn through an
# unbounded amount of attacker-supplied JSON.
MAX_IMPORT_SHOWS = 5000
MAX_IMPORT_EPISODES_PER_SHOW = 2000


@library_bp.route("/library")
@login_required
def view_library():
    items = (
        LibraryItem.query.filter_by(user_id=current_user.id)
        .order_by(LibraryItem.updated_at.desc())
        .all()
    )
    return render_template("library.html", items=items)


@library_bp.route("/library/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id):
    item = LibraryItem.query.get_or_404(item_id)
    if item.user_id != current_user.id:
        abort(403)

    db.session.delete(item)
    db.session.commit()
    flash("Removed from your library.", "success")
    return redirect(url_for("library.view_library"))


@library_bp.route("/library/import", methods=["POST"])
@login_required
def import_tvtime():
    """
    Imports a TV Time JSON export. TV Time uses TVDB ids, so each show is
    matched to a TMDB id via TMDB's /find endpoint before anything is saved.
    Shows that can't be matched are reported back, not silently dropped.
    """
    file = request.files.get("tvtime_file")
    if not file or file.filename == "":
        flash("Please choose a TV Time JSON export file.", "error")
        return redirect(url_for("library.view_library"))

    # Only accept files that look like a JSON export. Content-Type is
    # client-supplied so it isn't trusted on its own, but combined with
    # the extension check and the strict json.load below (which will
    # reject anything that isn't valid JSON) it rules out someone trying
    # to get an arbitrary file parsed by this endpoint.
    filename = file.filename.lower()
    if not filename.endswith(".json"):
        flash("Please upload a .json file exported from TV Time.", "error")
        return redirect(url_for("library.view_library"))

    try:
        data = json.load(file.stream)
    except (ValueError, UnicodeDecodeError):
        flash("That file doesn't look like valid JSON.", "error")
        return redirect(url_for("library.view_library"))

    if not isinstance(data, list):
        flash("Unexpected file format -- expected a list of shows.", "error")
        return redirect(url_for("library.view_library"))

    if len(data) > MAX_IMPORT_SHOWS:
        flash(f"That export has too many shows ({len(data)}) -- max is {MAX_IMPORT_SHOWS}.", "error")
        return redirect(url_for("library.view_library"))

    imported_shows = 0
    imported_episodes = 0
    not_found = []
    processed_tv_ids = set()

    # Load what already exists so re-running an import is safe (no duplicates)
    existing_checkins = {
        (c.tmdb_show_id, c.season_number, c.episode_number)
        for c in EpisodeCheckIn.query.filter_by(user_id=current_user.id).all()
    }
    existing_items = {
        (i.tmdb_id, i.media_type): i
        for i in LibraryItem.query.filter_by(user_id=current_user.id).all()
    }

    for show in data:
        if not isinstance(show, dict):
            continue

        tvdb_id = (show.get("id") or {}).get("tvdb")
        title = str(show.get("title") or "Unknown")[:255]

        if not tvdb_id:
            not_found.append(title)
            continue

        try:
            match = tmdb.find_tv_by_tvdb_id(tvdb_id)
        except Exception:
            match = None

        if not match:
            not_found.append(title)
            continue

        tmdb_id = match["tmdb_id"]
        poster_url = match.get("poster_url")
        # Just a seed value for brand-new items -- apply_status_from_counts
        # (below, once we've tallied this show's episodes) decides the real
        # status from what was actually watched in the export.
        status = STATUS_MAP.get(show.get("status"), "watching")

        item = existing_items.get((tmdb_id, "tv"))
        if not item:
            item = LibraryItem(
                user_id=current_user.id,
                tmdb_id=tmdb_id,
                media_type="tv",
                title=title,
                poster_path=poster_url,
                status=status,
            )
            db.session.add(item)
            existing_items[(tmdb_id, "tv")] = item
        elif not item.poster_path and poster_url:
            # Backfill posters for shows imported before this fix
            item.poster_path = poster_url

        imported_shows += 1
        processed_tv_ids.add(tmdb_id)

        seasons = show.get("seasons")
        if not isinstance(seasons, list):
            seasons = []

        episodes_seen_for_show = 0
        total_regular_episodes = 0
        watched_regular_episodes = 0

        for season in seasons:
            if not isinstance(season, dict):
                continue
            season_number = season.get("number", 0)
            episodes = season.get("episodes")
            if not isinstance(episodes, list):
                continue

            for ep in episodes:
                if episodes_seen_for_show >= MAX_IMPORT_EPISODES_PER_SHOW:
                    break
                episodes_seen_for_show += 1

                if not isinstance(ep, dict) or not isinstance(season_number, int):
                    continue

                episode_number = ep.get("number")
                is_watched = bool(ep.get("is_watched"))

                # Tally against the export's own counts (season 0 /
                # specials excluded, same as everywhere else in the app)
                # so "all episodes watched" is judged purely from what TV
                # Time itself says exists -- no TMDB cross-referencing, so
                # no risk of a TVDB/TMDB season-numbering mismatch (very
                # common for anime) silently blocking "completed".
                if season_number > 0 and isinstance(episode_number, int):
                    total_regular_episodes += 1
                    if is_watched:
                        watched_regular_episodes += 1

                if not is_watched:
                    continue
                if not isinstance(episode_number, int):
                    continue

                key = (tmdb_id, season_number, episode_number)
                if key in existing_checkins:
                    continue

                watched_at = None
                if ep.get("watched_at"):
                    try:
                        watched_at = datetime.strptime(str(ep["watched_at"]), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        watched_at = None

                checkin = EpisodeCheckIn(
                    user_id=current_user.id,
                    tmdb_show_id=tmdb_id,
                    show_title=title,
                    season_number=season_number,
                    episode_number=episode_number,
                    episode_name=str(ep.get("name") or "")[:255] or None,
                    watched_at=watched_at or datetime.utcnow(),
                )
                db.session.add(checkin)
                existing_checkins.add(key)
                imported_episodes += 1

        # Decide this show's status from the export's own totals -- fully
        # watched (per TV Time) -> completed, nothing watched -> Interested,
        # otherwise -> watching. This replaces the STATUS_MAP guess above.
        apply_status_from_counts(item, total_regular_episodes, watched_regular_episodes)

    db.session.commit()

    message = f"Imported {imported_shows} shows and {imported_episodes} watched episodes."
    if not_found:
        preview = ", ".join(not_found[:10])
        more = f" and {len(not_found) - 10} more" if len(not_found) > 10 else ""
        message += f" Couldn't match {len(not_found)} show(s) to TMDB: {preview}{more}."

    flash(message, "success" if imported_shows else "error")
    return redirect(url_for("library.view_library"))