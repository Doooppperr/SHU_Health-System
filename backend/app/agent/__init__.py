from flask import Blueprint


agent_bp = Blueprint("agent", __name__)


from . import routes  # noqa: E402,F401
