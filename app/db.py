"""PostgreSQL access."""
import logging
import time

import psycopg
from psycopg.errors import InvalidPassword, UniqueViolation  # noqa: F401  (re-exported)
from psycopg.rows import dict_row

from . import config

log = logging.getLogger(__name__)


def _open():
    return psycopg.connect(
        host=config.get("DB_HOST"),
        port=int(config.get("DB_PORT", "5432")),
        dbname=config.get("DB_NAME"),
        user=config.get("DB_USER"),
        password=config.get("DB_PASSWORD"),
        sslmode=config.get("DB_SSLMODE", "prefer"),
        connect_timeout=5,
        row_factory=dict_row,
    )


def connect(retries=10, delay=5):
    """One short connection per request. User and password are read from the
    mounted secret files each time, so a rotated login is picked up. Right after
    a rotation the file can lag by up to the CSI poll interval, so a password
    failure is retried a few times (the files are re-read on every attempt)."""
    for attempt in range(1, retries + 1):
        try:
            return _open()
        except InvalidPassword:
            if attempt == retries:
                raise
            log.warning("db password rejected, waiting for rotated secret (%s/%s)", attempt, retries)
            time.sleep(delay)


SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        name          TEXT NOT NULL,
        email         TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        pw_version    INTEGER NOT NULL DEFAULT 1,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until  TIMESTAMPTZ,
        created_at    TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS challenges (
        id         TEXT PRIMARY KEY,
        purpose    TEXT NOT NULL,
        email      TEXT NOT NULL,
        name       TEXT,
        user_id    TEXT,
        new_email  TEXT,
        code_hash  TEXT NOT NULL,
        code2_hash TEXT,
        attempts   INTEGER NOT NULL DEFAULT 0,
        verified   BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS challenges_email_purpose ON challenges (email, purpose)",
]


def init_schema(retries=30, delay=2):
    """Create the tables. Retries because the database may still be starting,
    and because two pods can start at the same moment."""
    for attempt in range(1, retries + 1):
        try:
            with connect() as conn:
                for statement in SCHEMA:
                    conn.execute(statement)
            log.info("database schema ready")
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("schema setup attempt %s/%s failed: %s", attempt, retries, exc)
            time.sleep(delay)
    raise SystemExit("database not reachable, giving up")
