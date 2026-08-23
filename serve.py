"""
Production entrypoint, driven by config.ini.

    python serve.py

`app.py` reads config.ini relative to the working directory, so run this from
the install root (the systemd unit sets WorkingDirectory). Ports, the bind
address and the TLS material all come from config.ini; the only environment
needed is the secrets in the unit's EnvironmentFile.

Plain HTTP is served by waitress. TLS, when [HTTPS] enabled = true, is served
by cheroot, which terminates it in-process -- waitress cannot. With both
enabled, one process answers on both ports.
"""
import os
import sys
import threading

import waitress

from app import SERVER_CONFIG, app


def _missing_tls_files(cert: str, key: str) -> list:
    return [p or "(unset)" for p in (cert, key) if not p or not os.path.exists(p)]


def _https_server(host: str, port: int, cert: str, key: str):
    from cheroot.ssl.builtin import BuiltinSSLAdapter
    from cheroot.wsgi import Server

    server = Server((host, port), app, server_name="rad-ticketing")
    server.ssl_adapter = BuiltinSSLAdapter(cert, key)
    return server


def _serve_http_in_thread(host: str, port: int):
    threading.Thread(
        target=waitress.serve,
        args=(app,),
        kwargs={"host": host, "port": port, "ident": "rad-ticketing"},
        daemon=True,
    ).start()


def main() -> int:
    host = SERVER_CONFIG["bind"]
    http_port = int(os.environ.get("FLASK_RUN_PORT") or SERVER_CONFIG["http_port"])

    if not SERVER_CONFIG["https_enabled"]:
        app.logger.info("Serving HTTP on %s:%s", host, http_port)
        waitress.serve(app, host=host, port=http_port, ident="rad-ticketing")
        return 0

    cert, key = SERVER_CONFIG["ssl_cert"], SERVER_CONFIG["ssl_key"]
    missing = _missing_tls_files(cert, key)
    if missing:
        # Falling back to plaintext here would silently expose logins.
        print(
            f"config.ini enables HTTPS but these files are missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    try:
        server = _https_server(host, SERVER_CONFIG["https_port"], cert, key)
    except ImportError:
        print(
            "TLS needs cheroot: .venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    if SERVER_CONFIG["http_enabled"]:
        app.logger.info("Serving HTTP on %s:%s", host, http_port)
        _serve_http_in_thread(host, http_port)

    app.logger.info("Serving HTTPS on %s:%s", host, SERVER_CONFIG["https_port"])
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
