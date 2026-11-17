-- Run once as the DB admin (signuprdsmaster) BEFORE the app starts:
--   psql "host=<endpoint> dbname=signuprds user=signuprdsmaster sslmode=require" -f schema.sql
-- Tables created by the admin automatically get the app user's privileges
-- through the ALTER DEFAULT PRIVILEGES statement run when the user was created.
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    pw_version    INTEGER NOT NULL DEFAULT 1,
    failed_logins INTEGER NOT NULL DEFAULT 0,
    locked_until  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS challenges (
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
);

CREATE INDEX IF NOT EXISTS challenges_email_purpose ON challenges (email, purpose);

-- Safety net if the tables were created before the default privileges existed:
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO signup_app;
