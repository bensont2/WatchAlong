from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.friends import friends_bp
from app.extensions import db
from app.models import Friendship, User, LibraryItem


def _are_friends(user_id_a, user_id_b):
    return (
        Friendship.query.filter(
            db.or_(
                db.and_(Friendship.requester_id == user_id_a, Friendship.recipient_id == user_id_b),
                db.and_(Friendship.requester_id == user_id_b, Friendship.recipient_id == user_id_a),
            ),
            Friendship.status == "accepted",
        ).first()
        is not None
    )


@friends_bp.route("/friends")
@login_required
def list_friends():
    accepted = Friendship.query.filter(
        db.or_(
            Friendship.requester_id == current_user.id,
            Friendship.recipient_id == current_user.id,
        ),
        Friendship.status == "accepted",
    ).all()

    friends = [
        {
            "friendship_id": f.id,
            "user": f.recipient if f.requester_id == current_user.id else f.requester,
        }
        for f in accepted
    ]

    incoming = Friendship.query.filter_by(
        recipient_id=current_user.id, status="pending"
    ).all()

    outgoing = Friendship.query.filter_by(
        requester_id=current_user.id, status="pending"
    ).all()

    return render_template(
        "friends.html", friends=friends, incoming=incoming, outgoing=outgoing
    )


@friends_bp.route("/friends/request", methods=["POST"])
@login_required
def send_request():
    username = request.form.get("username", "").strip()
    target = User.query.filter_by(username=username).first()

    if not target:
        flash(f"No user found with username \"{username}\".", "error")
        return redirect(url_for("friends.list_friends"))

    if target.id == current_user.id:
        flash("You can't send a friend request to yourself.", "error")
        return redirect(url_for("friends.list_friends"))

    existing = Friendship.query.filter(
        db.or_(
            db.and_(Friendship.requester_id == current_user.id, Friendship.recipient_id == target.id),
            db.and_(Friendship.requester_id == target.id, Friendship.recipient_id == current_user.id),
        )
    ).first()

    if existing:
        if existing.status == "accepted":
            flash(f"You're already friends with {target.username}.", "error")
        else:
            flash(f"There's already a pending request with {target.username}.", "error")
        return redirect(url_for("friends.list_friends"))

    friendship = Friendship(requester_id=current_user.id, recipient_id=target.id)
    db.session.add(friendship)
    db.session.commit()
    flash(f"Friend request sent to {target.username}.", "success")
    return redirect(url_for("friends.list_friends"))


@friends_bp.route("/friends/<int:friendship_id>/accept", methods=["POST"])
@login_required
def accept_request(friendship_id):
    friendship = Friendship.query.get_or_404(friendship_id)

    if friendship.recipient_id != current_user.id or friendship.status != "pending":
        abort(403)

    friendship.status = "accepted"
    friendship.responded_at = datetime.utcnow()
    db.session.commit()
    flash(f"You're now friends with {friendship.requester.username}.", "success")
    return redirect(url_for("friends.list_friends"))


@friends_bp.route("/friends/<int:friendship_id>/decline", methods=["POST"])
@login_required
def decline_request(friendship_id):
    friendship = Friendship.query.get_or_404(friendship_id)

    if friendship.recipient_id != current_user.id or friendship.status != "pending":
        abort(403)

    db.session.delete(friendship)
    db.session.commit()
    flash("Friend request declined.", "success")
    return redirect(url_for("friends.list_friends"))


@friends_bp.route("/friends/<int:friendship_id>/remove", methods=["POST"])
@login_required
def remove_friend(friendship_id):
    """Also used to cancel your own outgoing pending request."""
    friendship = Friendship.query.get_or_404(friendship_id)

    if current_user.id not in (friendship.requester_id, friendship.recipient_id):
        abort(403)

    db.session.delete(friendship)
    db.session.commit()
    flash("Removed.", "success")
    return redirect(url_for("friends.list_friends"))


@friends_bp.route("/friends/<int:user_id>/library")
@login_required
def friend_library(user_id):
    if not _are_friends(current_user.id, user_id):
        abort(403)

    friend = User.query.get_or_404(user_id)
    items = (
        LibraryItem.query.filter_by(user_id=user_id)
        .order_by(LibraryItem.updated_at.desc())
        .all()
    )

    return render_template("friend_library.html", friend=friend, items=items)