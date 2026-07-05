from flask import render_template
from flask_login import login_required

from app.activity import activity_bp
from app.extensions import db
from app.models import LibraryItem, EpisodeCheckIn, User


@activity_bp.route("/activity")
@login_required
def feed():
    ratings = (
        db.session.query(LibraryItem, User.username)
        .join(User, LibraryItem.user_id == User.id)
        .filter(LibraryItem.is_public.is_(True), LibraryItem.rating.isnot(None))
        .order_by(LibraryItem.updated_at.desc())
        .limit(30)
        .all()
    )

    checkins = (
        db.session.query(EpisodeCheckIn, User.username)
        .join(User, EpisodeCheckIn.user_id == User.id)
        .filter(EpisodeCheckIn.is_public.is_(True))
        .order_by(EpisodeCheckIn.watched_at.desc())
        .limit(30)
        .all()
    )

    events = []
    for item, username in ratings:
        events.append({
            "type": "rating",
            "username": username,
            "title": item.title,
            "media_type": item.media_type,
            "tmdb_id": item.tmdb_id,
            "rating": item.rating,
            "comment": item.comment,
            "timestamp": item.updated_at,
        })

    for checkin, username in checkins:
        events.append({
            "type": "episode",
            "username": username,
            "title": checkin.show_title,
            "tmdb_id": checkin.tmdb_show_id,
            "season_number": checkin.season_number,
            "episode_number": checkin.episode_number,
            "episode_name": checkin.episode_name,
            "comment": checkin.comment,
            "timestamp": checkin.watched_at,
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    events = events[:40]

    return render_template("activity.html", events=events)