"""Read a setting from NAME_FILE (a mounted secret file) or from NAME."""
import os


def get(name, default=None, required=True):
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    value = os.environ.get(name, default)
    if value is None and required:
        raise KeyError(f"{name} or {name}_FILE must be set")
    return value
