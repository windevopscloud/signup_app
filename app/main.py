"""Sign-up demo: register with e-mail, 4-digit confirmation, password, login,
password reset and e-mail change (a code to the old and one to the new address)."""
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask import (Flask, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import config, db, mailer

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)      # a code is valid for 10 minutes
RESEND_WAIT = timedelta(seconds=60)   # at most one new code per minute
MAX_ATTEMPTS = 5                      # wrong guesses allowed per code
MAX_FAILED_LOGINS = 5
LOCK_TIME = timedelta(minutes=15)
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
DUMMY_HASH = generate_password_hash("timing-equaliser")


class RateLimited(Exception):
    pass


def now():
    return datetime.now(timezone.utc)


def norm_email(value):
    value = (value or "").strip().lower()
    return value if len(value) <= 254 and EMAIL_RE.match(value) else None


def make_code():
    return f"{secrets.randbelow(10000):04d}"


def chash(cid, label, code):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{cid}|{label}|{code}".encode(), hashlib.sha256).hexdigest()


def code_matches(ch, label, column, code):
    if not ch[column]:
        return False
    return hmac.compare_digest(ch[column], chash(ch["id"], label, code))


# ---------------------------------------------------------------- challenges
def create_challenge(conn, purpose, email, *, name=None, user_id=None,
                     new_email=None, two_codes=False):
    t = now()
    conn.execute("DELETE FROM challenges WHERE expires_at < %s", (t,))
    recent = conn.execute(
        "SELECT 1 AS x FROM challenges WHERE email=%s AND purpose=%s AND created_at > %s",
        (email, purpose, t - RESEND_WAIT)).fetchone()
    if recent:
        raise RateLimited()
    conn.execute("DELETE FROM challenges WHERE email=%s AND purpose=%s", (email, purpose))
    cid = uuid.uuid4().hex
    code1 = make_code()
    code2 = make_code() if two_codes else None
    conn.execute(
        "INSERT INTO challenges (id, purpose, email, name, user_id, new_email, code_hash,"
        " code2_hash, attempts, verified, created_at, expires_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)",
        (cid, purpose, email, name, user_id, new_email, chash(cid, "a", code1),
         chash(cid, "b", code2) if code2 else None, False, t, t + CODE_TTL))
    return cid, code1, code2


def load_challenge(conn, purposes):
    cid = session.get("cid")
    if not cid:
        return None
    ch = conn.execute("SELECT * FROM challenges WHERE id=%s AND expires_at > %s",
                      (cid, now())).fetchone()
    return ch if ch and ch["purpose"] in purposes else None


def count_attempt(conn, ch):
    """Count one guess; return how many guesses are left."""
    conn.execute("UPDATE challenges SET attempts = attempts + 1 WHERE id=%s", (ch["id"],))
    return MAX_ATTEMPTS - (ch["attempts"] + 1)


def drop_challenge(conn, ch):
    conn.execute("DELETE FROM challenges WHERE id=%s", (ch["id"],))
    session.pop("cid", None)


def try_send(to, subject, body):
    try:
        mailer.send(to, subject, body)
        return True
    except Exception:  # noqa: BLE001
        log.exception("could not send mail")
        return False


def current_user(conn):
    uid = session.get("uid")
    if not uid:
        return None
    user = conn.execute("SELECT id, name, email, pw_version FROM users WHERE id=%s",
                        (uid,)).fetchone()
    if user is None or user["pw_version"] != session.get("pwv"):
        session.clear()
        return None
    return user


def check_new_password(p1, p2):
    if p1 != p2:
        return "The two passwords are not the same."
    if len(p1) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    return None


# ----------------------------------------------------------------------- app
def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.get("SECRET_KEY"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "true").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect_posts():
        if request.method == "POST":
            sent = request.form.get("csrf", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
                abort(400)

    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'")
        return resp

    # ------------------------------------------------------------- health
    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.get("/readyz")
    def readyz():
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1")
        except Exception:  # noqa: BLE001
            return "database unavailable", 503
        return "ok"

    # --------------------------------------------------- home / dashboard
    @app.get("/")
    def index():
        with db.connect() as conn:
            user = current_user(conn)
        if user:
            return render_template("dashboard.html", user=user)
        return render_template("index.html")

    # ------------------------------------------------- 1-2. register + code
    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render_template("register.html")
        name = " ".join(request.form.get("name", "").split())
        email = norm_email(request.form.get("email"))
        if not 1 <= len(name) <= 80 or not email:
            flash("Enter your name and a valid e-mail address.", "error")
            return render_template("register.html"), 400
        with db.connect() as conn:
            exists = conn.execute("SELECT 1 AS x FROM users WHERE email=%s", (email,)).fetchone()
            try:
                cid, code, _ = create_challenge(conn, "register", email, name=name)
            except RateLimited:
                flash("A code was sent a moment ago. Wait a minute before asking again.", "error")
                return render_template("register.html"), 429
        # An address that is already registered gets the same screen but no mail,
        # so the form does not reveal who has an account.
        if not exists and not try_send(
                email, "Your confirmation code",
                f"Hello {name},\n\nYour confirmation code is {code}.\n"
                f"It is valid for 10 minutes.\n"):
            flash("We could not send the e-mail. Please try again later.", "error")
            return render_template("register.html"), 502
        session["cid"] = cid
        flash("If the address can be used, a 4-digit code is on its way.", "info")
        return redirect(url_for("verify"))

    # --------------------------------------------- 3. confirm the 4 digits
    @app.route("/verify", methods=["GET", "POST"])
    def verify():
        with db.connect() as conn:
            ch = load_challenge(conn, ("register", "reset"))
            if ch is None:
                flash("That code has expired or is not valid. Please start again.", "error")
                return redirect(url_for("index"))
            if request.method == "POST":
                code = request.form.get("code", "").strip()
                if ch["attempts"] >= MAX_ATTEMPTS:
                    drop_challenge(conn, ch)
                    flash("Too many wrong codes. Please start again.", "error")
                    return redirect(url_for("index"))
                left = count_attempt(conn, ch)
                if code_matches(ch, "a", "code_hash", code):
                    conn.execute("UPDATE challenges SET verified=%s WHERE id=%s", (True, ch["id"]))
                    return redirect(url_for("set_password"))
                if left <= 0:
                    drop_challenge(conn, ch)
                    flash("Too many wrong codes. Please start again.", "error")
                    return redirect(url_for("index"))
                flash(f"That code is not right. {left} tries left.", "error")
        return render_template("verify.html", email=ch["email"])

    # ------------------------------------------------ 4. set the password
    @app.route("/set-password", methods=["GET", "POST"])
    def set_password():
        with db.connect() as conn:
            ch = load_challenge(conn, ("register", "reset"))
            if ch is None or not ch["verified"]:
                flash("Please confirm the code first.", "error")
                return redirect(url_for("index"))
            if request.method == "POST":
                p1, p2 = request.form.get("password", ""), request.form.get("password2", "")
                problem = check_new_password(p1, p2)
                if problem:
                    flash(problem, "error")
                    return render_template("set_password.html", purpose=ch["purpose"]), 400
                hashed = generate_password_hash(p1)
                if ch["purpose"] == "register":
                    try:
                        conn.execute(
                            "INSERT INTO users (id, name, email, password_hash, created_at)"
                            " VALUES (%s,%s,%s,%s,%s)",
                            (uuid.uuid4().hex, ch["name"], ch["email"], hashed, now()))
                    except db.UniqueViolation:
                        conn.rollback()
                        flash("This address cannot be registered. Try logging in.", "error")
                        conn.execute("DELETE FROM challenges WHERE id=%s", (ch["id"],))
                        session.pop("cid", None)
                        return redirect(url_for("login"))
                elif ch["user_id"]:
                    conn.execute(
                        "UPDATE users SET password_hash=%s, pw_version=pw_version+1,"
                        " failed_logins=0, locked_until=NULL WHERE id=%s", (hashed, ch["user_id"]))
                drop_challenge(conn, ch)
                flash("Password saved. You can log in now.", "info")
                return redirect(url_for("login"))
        return render_template("set_password.html", purpose=ch["purpose"])

    # ----------------------------------------------------------- 5. login
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")
        email = norm_email(request.form.get("email"))
        password = request.form.get("password", "")
        failed = "E-mail or password is not right."
        with db.connect() as conn:
            user = conn.execute(
                "SELECT *, (locked_until IS NOT NULL AND locked_until > %s) AS locked"
                " FROM users WHERE email=%s", (now(), email or "")).fetchone()
            if user is None:
                check_password_hash(DUMMY_HASH, password)   # keep timing similar
                flash(failed, "error")
                return render_template("login.html"), 401
            if user["locked"]:
                flash("Too many attempts. Try again in a few minutes.", "error")
                return render_template("login.html"), 429
            if not check_password_hash(user["password_hash"], password):
                count = user["failed_logins"] + 1
                if count >= MAX_FAILED_LOGINS:
                    conn.execute("UPDATE users SET failed_logins=0, locked_until=%s WHERE id=%s",
                                 (now() + LOCK_TIME, user["id"]))
                else:
                    conn.execute("UPDATE users SET failed_logins=%s WHERE id=%s",
                                 (count, user["id"]))
                flash(failed, "error")
                return render_template("login.html"), 401
            conn.execute("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=%s",
                         (user["id"],))
        session.clear()
        session.permanent = True
        session["uid"] = user["id"]
        session["pwv"] = user["pw_version"]
        return redirect(url_for("index"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # ------------------------------------------------- 7. reset password
    @app.route("/forgot", methods=["GET", "POST"])
    def forgot():
        if request.method == "GET":
            return render_template("forgot.html")
        email = norm_email(request.form.get("email"))
        if not email:
            flash("Enter a valid e-mail address.", "error")
            return render_template("forgot.html"), 400
        with db.connect() as conn:
            user = conn.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone()
            if not user:
                flash("No account found with that e-mail address.", "error")
                return render_template("forgot.html"), 404
            try:
                cid, code, _ = create_challenge(conn, "reset", email,
                                                user_id=user["id"])
            except RateLimited:
                flash("A code was sent a moment ago. Wait a minute before asking again.", "error")
                return render_template("forgot.html"), 429
        if not try_send(email, "Your password reset code",
                                 f"Your password reset code is {code}.\n"
                                 f"It is valid for 10 minutes.\n"
                                 f"If you did not ask for this, ignore this e-mail.\n"):
            flash("We could not send the e-mail. Please try again later.", "error")
            return render_template("forgot.html"), 502
        session["cid"] = cid
        flash("A 4-digit code is on its way to your e-mail.", "info")
        return redirect(url_for("verify"))

    # ------------------------- reset password while logged in (no e-mail form)
    @app.post("/account/password")
    def account_password():
        with db.connect() as conn:
            user = current_user(conn)
            if not user:
                return redirect(url_for("login"))
            try:
                cid, code, _ = create_challenge(conn, "reset", user["email"],
                                                user_id=user["id"])
            except RateLimited:
                flash("A code was sent a moment ago. Wait a minute before asking again.", "error")
                return redirect(url_for("index"))
        if not try_send(user["email"], "Your password reset code",
                        f"Your password reset code is {code}.\n"
                        f"It is valid for 10 minutes.\n"
                        f"If you did not ask for this, ignore this e-mail.\n"):
            flash("We could not send the e-mail. Please try again later.", "error")
            return redirect(url_for("index"))
        session["cid"] = cid
        flash("We sent a 4-digit code to your registered e-mail.", "info")
        return redirect(url_for("verify"))

    # ------------------------------------- 8-9. change e-mail, two codes
    @app.route("/account/email", methods=["GET", "POST"])
    def change_email():
        with db.connect() as conn:
            user = current_user(conn)
            if not user:
                return redirect(url_for("login"))
            if request.method == "GET":
                return render_template("change_email.html", user=user)
            new_email = norm_email(request.form.get("new_email"))
            if not new_email or new_email == user["email"]:
                flash("Enter a different, valid e-mail address.", "error")
                return render_template("change_email.html", user=user), 400
            taken = conn.execute("SELECT 1 AS x FROM users WHERE email=%s", (new_email,)).fetchone()
            if taken:
                flash("That address cannot be used.", "error")
                return render_template("change_email.html", user=user), 400
            try:
                cid, code_old, code_new = create_challenge(
                    conn, "email_change", user["email"], user_id=user["id"],
                    new_email=new_email, two_codes=True)
            except RateLimited:
                flash("Codes were sent a moment ago. Wait a minute before asking again.", "error")
                return render_template("change_email.html", user=user), 429
        sent_old = try_send(user["email"], "Confirm your e-mail change",
                            f"Someone asked to change your e-mail address to {new_email}.\n"
                            f"Your code is {code_old}. It is valid for 10 minutes.\n"
                            f"If this was not you, do not share the code.\n")
        sent_new = try_send(new_email, "Confirm your new e-mail address",
                            f"Your code is {code_new}. It is valid for 10 minutes.\n")
        if not (sent_old and sent_new):
            flash("We could not send both e-mails. Please try again later.", "error")
            return render_template("change_email.html", user=user), 502
        session["cid"] = cid
        flash("A code was sent to your current address and another to the new one.", "info")
        return redirect(url_for("change_email_verify"))

    @app.route("/account/email/verify", methods=["GET", "POST"])
    def change_email_verify():
        with db.connect() as conn:
            user = current_user(conn)
            if not user:
                return redirect(url_for("login"))
            ch = load_challenge(conn, ("email_change",))
            if ch is None or ch["user_id"] != user["id"]:
                flash("That request has expired. Please start again.", "error")
                return redirect(url_for("change_email"))
            if request.method == "POST":
                old_code = request.form.get("old_code", "").strip()
                new_code = request.form.get("new_code", "").strip()
                if ch["attempts"] >= MAX_ATTEMPTS:
                    drop_challenge(conn, ch)
                    flash("Too many wrong codes. Please start again.", "error")
                    return redirect(url_for("change_email"))
                left = count_attempt(conn, ch)
                ok_old = code_matches(ch, "a", "code_hash", old_code)
                ok_new = code_matches(ch, "b", "code2_hash", new_code)
                if ok_old and ok_new:
                    old_email = user["email"]
                    try:
                        conn.execute("UPDATE users SET email=%s WHERE id=%s",
                                     (ch["new_email"], user["id"]))
                    except db.UniqueViolation:
                        conn.rollback()
                        flash("That address cannot be used.", "error")
                        return redirect(url_for("change_email"))
                    drop_challenge(conn, ch)
                    try_send(old_email, "Your e-mail address was changed",
                             f"The e-mail address of your account was changed to {ch['new_email']}.\n")
                    flash("Your e-mail address is updated.", "info")
                    return redirect(url_for("index"))
                if left <= 0:
                    drop_challenge(conn, ch)
                    flash("Too many wrong codes. Please start again.", "error")
                    return redirect(url_for("change_email"))
                flash(f"One or both codes are not right. {left} tries left.", "error")
        return render_template("change_email_verify.html", old_email=user["email"],
                               new_email=ch["new_email"])

    return app
