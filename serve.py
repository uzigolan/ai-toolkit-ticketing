"""
Production entrypoint: serve the app with waitress, driven by config.ini.

    python serve.py

`app.py` reads config.ini relative to the working directory, so run this from
the install root (the systemd unit sets WorkingDirectory). Ports and the bind
address come from [SERVER]; nothing here needs environment variables beyond the
secrets in the EnvironmentFile.

waitress does not terminate TLS. With [HTTPS] enabled = true this refuses to
start rather than serving logins in plaintext -- terminate TLS at nginx/Apache
in front, or run app.py directly for the self-terminating dev setup.
"""
import os
import sys

from waitress import serve

from app import SERVER_CONFIG, app


def main() -> int:
    if SERVER_CONFIG["https_enabled"]:
        print(
            "config.ini enables [HTTPS], but serve.py runs waitress, which does not "
            "terminate TLS. Set [HTTPS] enabled = false and terminate TLS at a "
            "reverse proxy, or run app.py directly.",
            file=sys.stderr,
        )
        return 1

    host = SERVER_CONFIG["bind"]
    port = int(os.environ.get("FLASK_RUN_PORT") or SERVER_CONFIG["http_port"])
    app.logger.info("Serving HTTP on %s:%s", host, port)
    serve(app, host=host, port=port, ident="rad-ticketing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
