"""Container entrypoint: gunicorn over HTTPS on 8443 (self-signed pair made at start; the ALB does not validate it).
Local development stays plain: see docker-compose.yml (port 8000)."""
import os

from .tls import make_self_signed


def main():
    cert, key = make_self_signed(os.environ.get("TLS_DIR", "/tmp/tls"))
    port = os.environ.get("PORT", "8443")
    os.execvp("gunicorn", ["gunicorn", "--bind", f"0.0.0.0:{port}", "--workers", "2",
                           "--certfile", cert, "--keyfile", key, "--access-logfile", "-",
                           "app.main:create_app()"])


if __name__ == "__main__":
    main()
