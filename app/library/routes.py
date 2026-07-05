from flask import render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.library import library_bp
from app.extensions import db
from app.models import LibraryItem


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
