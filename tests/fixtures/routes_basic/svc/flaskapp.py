"""Decorator routes and add_url_rule, flask style."""
from flask import Blueprint, Flask

app = Flask(__name__)
bp = Blueprint("admin", __name__)


@app.route("/health")
def health():
    """Liveness probe."""
    return "ok"


@bp.route("/users/<int:user_id>", methods=("GET", "delete"))
def user(user_id):
    """One user."""
    return ""


def export():
    """Export everything."""
    return ""


app.add_url_rule("/export", view_func=export, methods=["POST"])


def make(methods):
    @app.route("/dynamic", methods=methods)
    def dynamic():
        return ""
    return dynamic
