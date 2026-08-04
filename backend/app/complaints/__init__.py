from flask import Blueprint


complaints_bp = Blueprint("complaints", __name__)

from . import routes  # noqa: E402,F401
