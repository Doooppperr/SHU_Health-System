from flask import Blueprint


oauth_bp = Blueprint("oauth", __name__)


from . import routes  # noqa: E402,F401
