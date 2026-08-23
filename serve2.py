"""
Production entrypoint: serve the app with cheroot, driven by config.ini.

    python serve.py

`app.py` reads config.ini relative to the working directory, so run this from
the install root (the systemd unit sets WorkingDirectory). Ports, the bind
address and the TLS material all come from config.ini; nothing here needs
environment variables beyond the secrets in the EnvironmentFile.

cheroot rather than waitress because it terminates TLS, so [HTTPS] enabled =
true works under systemd instead of forcing a reverse proxy. With both
listeners enabled, the plain one runs in a thread of the same process.
"""
import os
import sys
import threading

from cheroot.ssl.builtin import BuiltinSSLAdapter
from cheroot.wsgi import Server as WSGIServer

from app import SERVER_CONFIG, app


def _server(host: str, port: int) -> WSGIServer:
    return WSGIServer((host, port), app, server_name="rad-ticketing")


def main() -> int:
    host = SERVER_CONFIG["bind"]
    http_port = int(os.environ.get("FLASK_RUN_PORT") or SERVER_CONFIG["http_port"])

    tls = None
    if SERVER_CONFIG["https_enabled"]:
        cert, key = SERVER_CONFIG["ssl_cert"], SERVER_CONFIG["ssl_key"]
        missing = [p or "(unset)" for p in (cert, key) if not p or not os.path.exists(p)]
        if missing:
            # Falling back to plaintext here would silently expose logins.
            print(
                "config.ini enables HTTPS but these files are missing: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 1
        tls = BuiltinSSLAdapter(cert, key)

    servers = []
    if tls:
        https = _server(host, SERVER_CONFIG["https_port"])
        https.ssl_adapter = tls
        servers.append(("HTTPS", https))
    if SERVER_CONFIG["http_enabled"] or not tls:
        servers.append(("HTTP", _server(host, http_port)))

    for scheme, server in servers:
        app.logger.info("Serving %s on %s:%s", scheme, host, server.bind_addr[1])

    # The last listener runs in the foreground so systemd sees a live process.
    for _, server in servers[:-1]:
        threading.Thread(target=server.safe_start, daemon=True).start()

    main_server = servers[-1][1]
    try:
        main_server.safe_start()
    except KeyboardInterrupt:
        main_server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
