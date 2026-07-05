from flask import Blueprint

library_bp = Blueprint("library", __name__)

from app.library import routes  # noqa: E402,F401
