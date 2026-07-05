from datetime import datetime
import bcrypt
from flask_login import UserMixin

from app.extensions import db


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    library_items = db.relationship(
        "LibraryItem", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    episode_checkins = db.relationship(
        "EpisodeCheckIn", backref="user", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, raw_password):
        self.password_hash = bcrypt.hashpw(
            raw_password.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    def check_password(self, raw_password):
        return bcrypt.checkpw(
            raw_password.encode("utf-8"), self.password_hash.encode("utf-8")
        )


class LibraryItem(db.Model):
    """
    One row per (user, show). Tracks watch status for the series/movie as a
    whole. Rating + comment are optional -- you can track a show for months
    before ever rating it (matches how TV Time / Trakt-style trackers work).
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    tmdb_id = db.Column(db.Integer, nullable=False)
    media_type = db.Column(db.String(10), nullable=False)  # "movie" or "tv"
    title = db.Column(db.String(255), nullable=False)
    poster_path = db.Column(db.String(255))

    # watching | completed | plan_to_watch | dropped
    status = db.Column(db.String(20), default="watching", nullable=False)

    rating = db.Column(db.Integer, nullable=True)  # 1-5 stars, optional
    comment = db.Column(db.Text, nullable=True)
    is_public = db.Column(db.Boolean, default=False)

    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("user_id", "tmdb_id", "media_type", name="uq_user_show"),
    )


class EpisodeCheckIn(db.Model):
    """
    One row per (user, show, season, episode) that's been marked watched.
    Deleting the row = marking it unwatched (and clears any comment on it).
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    tmdb_show_id = db.Column(db.Integer, nullable=False)
    show_title = db.Column(db.String(255), nullable=False)  # cached for the feed
    season_number = db.Column(db.Integer, nullable=False)
    episode_number = db.Column(db.Integer, nullable=False)
    episode_name = db.Column(db.String(255))

    is_public = db.Column(db.Boolean, default=False)
    comment = db.Column(db.Text, nullable=True)
    watched_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "tmdb_show_id", "season_number", "episode_number",
            name="uq_user_episode",
        ),
    )

class Friendship(db.Model):
    """
    One row per friend request. requester_id sent it, recipient_id received
    it. status moves pending -> accepted (or gets deleted on decline/remove).
    Once accepted, either side can see the other's full library.
    """
    id = db.Column(db.Integer, primary_key=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)  # pending | accepted
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    responded_at = db.Column(db.DateTime, nullable=True)

    requester = db.relationship("User", foreign_keys=[requester_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

    __table_args__ = (
        db.UniqueConstraint("requester_id", "recipient_id", name="uq_friend_pair"),
    )