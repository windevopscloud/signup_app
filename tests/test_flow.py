"""End-to-end test of the whole journey. Uses SQLite through a small shim so it
runs without PostgreSQL; the SQL is the same text the app sends to Postgres.
Run:  python -m unittest tests.test_flow"""
import os
import re
import sqlite3
import sys
import types
import unittest
from datetime import datetime

# ---- fake psycopg so app.db imports without the real package -------------
class UniqueViolation(Exception):
    pass

psycopg = types.ModuleType("psycopg")
errors = types.ModuleType("psycopg.errors"); errors.UniqueViolation = UniqueViolation
rows = types.ModuleType("psycopg.rows"); rows.dict_row = None
psycopg.errors, psycopg.rows = errors, rows
psycopg.connect = None
sys.modules.update({"psycopg": psycopg, "psycopg.errors": errors, "psycopg.rows": rows})

sqlite3.register_adapter(datetime, lambda d: d.isoformat())

os.environ.update(SECRET_KEY="test-secret", COOKIE_SECURE="false", DB_HOST="x", DB_NAME="x",
                  DB_USER="x", DB_PASSWORD="x")

from app import db, mailer, main  # noqa: E402


class Conn:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?").replace("TIMESTAMPTZ", "TEXT")
        try:
            return self.raw.execute(sql, params)
        except sqlite3.IntegrityError as exc:
            raise UniqueViolation(str(exc)) from exc

    def rollback(self):
        self.raw.rollback()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.raw.commit() if et is None else self.raw.rollback()


class FileSecrets(unittest.TestCase):
    def test_file_wins_and_is_stripped(self):
        import tempfile
        from app import config
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("from-file\n")
        os.environ["X_TEST"] = "from-env"
        os.environ["X_TEST_FILE"] = fh.name
        self.assertEqual(config.get("X_TEST"), "from-file")
        del os.environ["X_TEST_FILE"]
        self.assertEqual(config.get("X_TEST"), "from-env")
        with self.assertRaises(KeyError):
            config.get("X_MISSING")
        self.assertIsNone(config.get("X_MISSING", required=False))


class Flow(unittest.TestCase):
    def setUp(self):
        raw = sqlite3.connect(":memory:", check_same_thread=False)
        raw.row_factory = sqlite3.Row
        raw.create_function  # noqa
        class RowConn(Conn):
            def execute(s, sql, params=()):
                cur = super().execute(sql, params)
                cur.row_factory = lambda c, r: {d[0]: r[i] for i, d in enumerate(c.description)}
                return cur
        self.conn = RowConn(raw)
        for stmt in db.SCHEMA:
            self.conn.execute(stmt.replace("TIMESTAMPTZ", "TEXT"))
        db.connect = lambda: self.conn
        self.mail = []
        mailer.send = lambda to, subj, body: self.mail.append((to, body))
        main.RESEND_WAIT = main.timedelta(seconds=0)
        self.c = main.create_app().test_client()

    def post(self, path, **data):
        with self.c.session_transaction() as s:
            s.setdefault("csrf", "tok")
            tok = s["csrf"]
        return self.c.post(path, data={"csrf": tok, **data}, follow_redirects=True)

    def last_code(self, to):
        for t, body in reversed(self.mail):
            if t == to:
                return re.search(r"code is (\d{4})", body).group(1)

    def register(self, name="Asha", email="asha@example.com", pw="correct-horse-1"):
        self.post("/register", name=name, email=email)
        self.post("/verify", code=self.last_code(email))
        return self.post("/set-password", password=pw, password2=pw)

    def test_full_journey(self):
        r = self.register()
        self.assertIn(b"Password saved", r.data)
        r = self.post("/login", email="asha@example.com", password="correct-horse-1")
        self.assertIn(b"Welcome, Asha", r.data)
        self.assertIn(b"asha@example.com", r.data)

        # change e-mail: one code to old, one to new
        self.post("/account/email", new_email="new@example.com")
        old_code, new_code = self.last_code("asha@example.com"), self.last_code("new@example.com")
        r = self.post("/account/email/verify", old_code=old_code, new_code="0000" if new_code != "0000" else "1111")
        self.assertIn(b"not right", r.data)
        r = self.post("/account/email/verify", old_code=old_code, new_code=new_code)
        self.assertIn(b"updated", r.data)
        self.assertIn(b"Asha", r.data)
        self.assertIn(b"new@example.com", r.data)
        self.assertNotIn(b"asha@example.com</strong>", r.data)

    def test_wrong_code_then_lockout_of_code(self):
        self.post("/register", name="B", email="b@example.com")
        good = self.last_code("b@example.com")
        bad = "0000" if good != "0000" else "1111"
        for _ in range(5):
            r = self.post("/verify", code=bad)
        self.assertIn(b"Too many", r.data)
        r = self.post("/verify", code=good)
        self.assertIn(b"expired or is not valid", r.data)

    def test_password_reset_logs_out_old_session(self):
        self.register()
        self.post("/login", email="asha@example.com", password="correct-horse-1")
        self.post("/forgot", email="asha@example.com")
        self.post("/verify", code=self.last_code("asha@example.com"))
        self.post("/set-password", password="brand-new-pass-2", password2="brand-new-pass-2")
        self.assertIn(b"Log in", self.c.get("/").data)       # old session invalidated
        r = self.post("/login", email="asha@example.com", password="correct-horse-1")
        self.assertIn(b"not right", r.data)
        r = self.post("/login", email="asha@example.com", password="brand-new-pass-2")
        self.assertIn(b"Welcome, Asha", r.data)

    def test_unknown_email_is_rejected_on_reset(self):
        r = self.post("/forgot", email="nobody@example.com")
        self.assertIn(b"No account found", r.data)
        self.assertNotIn(b"Enter the 4-digit code", r.data)
        self.assertEqual(self.mail, [])

    def test_csrf_required(self):
        r = self.c.post("/login", data={"email": "a@b.co", "password": "x"})
        self.assertEqual(r.status_code, 400)

    def test_account_lock_after_five_failures(self):
        self.register()
        for _ in range(5):
            self.post("/login", email="asha@example.com", password="wrong-password")
        r = self.post("/login", email="asha@example.com", password="correct-horse-1")
        self.assertIn(b"Too many attempts", r.data)

    def test_short_password_rejected(self):
        self.post("/register", name="C", email="c@example.com")
        self.post("/verify", code=self.last_code("c@example.com"))
        r = self.post("/set-password", password="short", password2="short")
        self.assertIn(b"at least 10", r.data)


if __name__ == "__main__":
    unittest.main()
