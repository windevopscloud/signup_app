import logging

from . import db

logging.basicConfig(level=logging.INFO)
db.init_schema()
