"""Send mail over SMTP (Amazon SES SMTP, Mailpit for local tests, ...)."""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def send(to, subject, body):
    host = os.environ.get("SMTP_HOST")
    if not host:
        if os.environ.get("DEV_PRINT_CODES") == "1":
            log.warning("MAIL to=%s subject=%s\n%s", to, subject, body)
            return
        raise RuntimeError("SMTP_HOST is not set")

    msg = EmailMessage()
    msg["From"] = os.environ["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as smtp:
        if os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
            smtp.starttls(context=ssl.create_default_context())
        user = config.get("SMTP_USER", required=False)
        if user:
            smtp.login(user, config.get("SMTP_PASSWORD"))
        smtp.send_message(msg)
