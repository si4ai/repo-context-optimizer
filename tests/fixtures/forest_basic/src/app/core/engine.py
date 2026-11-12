import json

from ..util import helpers
from app.util.helpers import clamp as _c


def run() -> int:
    return helpers.clamp(0) + _c(0)
