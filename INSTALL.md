# Install & Run

**Contents:** [Requirements](#requirements) ·
[Quick start (Windows)](#quick-start-windows) ·
[Quick start (Linux / macOS)](#quick-start-linux--macos) ·
[First login](#first-login) ·
[Configuration](#configuration) ·
[Serving HTTPS](#serving-https) ·
[Run as a Linux service](#run-as-a-linux-service) ·
[Behind a reverse proxy](#behind-a-reverse-proxy) ·
[Upgrading](#upgrading) ·
[Backup and restore](#backup-and-restore) ·
[Uninstall](#uninstall) ·
[Troubleshooting](#troubleshooting)

## Requirements

- Python 3.10 or newer
- No database server — SQLite is built into Python
- Optional: reachable LDAP/AD server, if you want directory logins
- Optional: PowerShell 5.1+ on Windows, for `start.ps1`

## Quick start (Windows)

From the repo root:

```powershell
.\start.ps1
```

That single script creates `.venv`, installs requirements when
`requirements.txt` changes, generates a persistent session key in
`.secret_key`, applies database migrations and starts the app in the
foreground. **Ctrl+C** stops it.

Useful switches:

| Switch | Effect |
| --- | --- |
| `-Port 8080` | Listen on another port |
| `-SkipInstall` | Skip the dependency check |
| `-Production` | Serve with waitress instead of the debug server |

If `python` on your PATH is the Microsoft Store stub, the script detects it and
tells you rather than failing halfway; install Python from python.org and re-run.

Doing it by hand instead:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:TICKETING_SECRET_KEY = "something-random"
.\.venv\Scripts\python.exe migrate_db.py
.\.venv\Scripts\python.exe app.py
```

## Quick start (Linux / macOS)

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TICKETING_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python migrate_db.py
python app.py
```

Browse to <http://localhost:5000>.

## First login

The first start creates a local admin because an app nobody can log into is
useless. The generated password is printed **once** to the log:

```
WARNING:app:Created first local admin 'admin' with generated password: <shown once>
```

Choose your own instead by setting these before the first run:

```bash
export TICKETING_BOOTSTRAP_ADMIN="uzi"
export TICKETING_BOOTSTRAP_PASSWORD="a-password-you-picked"
```

Change it afterwards at `/account/password`, and create the rest of the
accounts at `/admin/users`.

## Configuration

Everything is environment variables plus two files. Nothing is required for a
local-accounts-only install.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TICKETING_SECRET_KEY` | `change-me-in-config` | Signs session cookies. **Set this in production.** |
| `TICKETING_DB` | `tickets.sqlite` | Database file location |
| `TICKETING_CATEGORIES_FILE` | `categories.yml` | Taxonomy file location |
| `TICKETING_ADMINS` | *(empty)* | Comma-separated usernames seeded as admins on first sight |
| `TICKETING_BOOTSTRAP_ADMIN` | `admin` | Username of the first local admin |
| `TICKETING_BOOTSTRAP_PASSWORD` | *(generated)* | Password of the first local admin |
| `FLASK_RUN_PORT` | `5000` | Port for the development server when only one listener is enabled |

Files:

- **`categories.yml`** — toolkits, categories, severities and the rest of the
  form vocabulary. See [docs/configuration.md](docs/configuration.md).
- **`config.ini`** — ports, TLS material and LDAP. Copy `config.ini.example`
  and edit. Without it, the app serves plain HTTP on 5000 with local accounts
  only. See [docs/configuration.md](docs/configuration.md#configini) and
  [docs/authentication.md](docs/authentication.md).

| Section | Keys | Purpose |
| --- | --- | --- |
| `[SERVER]` | `http_enabled`, `http_port` | The plain listener |
| `[HTTPS]` | `enabled`, `port`, `ssl_cert`, `ssl_key` | TLS terminated by the app itself |
| `[LDAP]` | `enabled`, `LDAP_HOST`, … | Directory logins |

`config.ini` holds the LDAP service-account password and points at private
keys, so it is git-ignored. On a server, `chmod 600` it.

## Serving HTTPS

Login credentials cross the wire in the request body, so anything beyond
localhost needs TLS. Two ways to get it:

**Terminate in the app.** Put the certificate and key under `https/` — that
directory is git-ignored, so a private key can't be committed by accident —
and enable it:

```ini
[HTTPS]
enabled = true
port = 444
ssl_cert = https/tls.cert.pem
ssl_key = https/tls.key.pem
```

For a throwaway pair to test with:

```powershell
.\.venv\Scripts\python.exe scripts\make_self_signed_cert.py
```

If `enabled = true` and either file is missing, the app **refuses to start**
and names them, rather than quietly falling back to plaintext.

With `[SERVER] http_enabled = true` as well, one process answers on both ports
— useful while migrating bookmarks. Set it to `false` to serve TLS only.

**Or terminate in front of it**, which is the better answer for a real
deployment: leave `[HTTPS] enabled = false` and put nginx or Apache in front,
as below. The systemd unit runs waitress, which does not terminate TLS, so a
proxy is the expected setup there.

## Run as a Linux service

Tested on RHEL/Rocky and Debian/Ubuntu with systemd.

**1. Create a service account and install the code**

```bash
sudo useradd --system --home /opt/rad-ticketing --shell /sbin/nologin radticketing
sudo mkdir -p /opt/rad-ticketing
sudo cp -r . /opt/rad-ticketing
sudo chown -R radticketing:radticketing /opt/rad-ticketing
```

**2. Build the virtual environment**

```bash
sudo -u radticketing python3 -m venv /opt/rad-ticketing/.venv
sudo -u radticketing /opt/rad-ticketing/.venv/bin/pip install -r /opt/rad-ticketing/requirements.txt
sudo -u radticketing /opt/rad-ticketing/.venv/bin/pip install waitress
```

**3. Write the environment file**

```bash
sudo tee /etc/sysconfig/rad-ticketing >/dev/null <<EOF
TICKETING_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
TICKETING_DB=/opt/rad-ticketing/tickets.sqlite
TICKETING_CATEGORIES_FILE=/opt/rad-ticketing/categories.yml
TICKETING_ADMINS=uzi
TICKETING_PORT=5000
EOF
sudo chmod 600 /etc/sysconfig/rad-ticketing
sudo chown root:root /etc/sysconfig/rad-ticketing
```

On Debian/Ubuntu use `/etc/default/rad-ticketing` and change `EnvironmentFile`
in the unit accordingly.

**4. Install and start the unit**

```bash
sudo cp scripts/install/rad-ticketing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rad-ticketing
systemctl status rad-ticketing
sudo journalctl -u rad-ticketing -f      # the bootstrap password appears here
```

The unit runs `migrate_db.py` as `ExecStartPre`, so every start brings the
schema up to date, and a failed migration stops the unit rather than letting
the app serve against a half-built database.

## Behind a reverse proxy

The service listens on `127.0.0.1` by design — terminate TLS at nginx or Apache
in front of it. LDAP passwords cross the wire at login, so **do not serve this
over plain HTTP** beyond localhost.

```nginx
server {
    listen 443 ssl;
    server_name tickets.example.com;

    ssl_certificate     /etc/pki/tls/certs/tickets.crt;
    ssl_certificate_key /etc/pki/tls/private/tickets.key;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        client_max_body_size 10m;   # pasted chats can be long
    }
}
```

## Upgrading

```bash
sudo systemctl stop rad-ticketing
sudo -u radticketing cp /opt/rad-ticketing/tickets.sqlite /var/backups/tickets-$(date +%F).sqlite
# replace the code, keeping tickets.sqlite, config.ini and categories.yml
sudo -u radticketing /opt/rad-ticketing/.venv/bin/pip install -r requirements.txt
sudo systemctl start rad-ticketing
```

Migrations run on start and are idempotent, so an upgrade that adds no schema
changes is a no-op. Check what a database has had applied with:

```bash
/opt/rad-ticketing/.venv/bin/python migrate_db.py --status
```

## Backup and restore

Everything lives in one SQLite file plus two config files:

```bash
sqlite3 /opt/rad-ticketing/tickets.sqlite ".backup '/var/backups/tickets.sqlite'"
cp /opt/rad-ticketing/{config.ini,categories.yml} /var/backups/
```

Use `.backup` rather than `cp` while the service is running — it takes a
consistent snapshot instead of a possibly torn file. Restore by stopping the
service, putting the files back and starting it again.

## Uninstall

```bash
sudo systemctl disable --now rad-ticketing
sudo rm /etc/systemd/system/rad-ticketing.service /etc/sysconfig/rad-ticketing
sudo systemctl daemon-reload
sudo rm -rf /opt/rad-ticketing          # this deletes the ticket database
sudo userdel radticketing
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Python was not found` on Windows | PATH points at the Store stub. Use `py -3`, or turn off the aliases in Settings → Apps → Advanced app settings → App execution aliases. |
| Everyone is logged out after a restart | `TICKETING_SECRET_KEY` changed between runs. Set it permanently. |
| Login always fails, no LDAP errors | `config.ini` has `enabled = false`, or no `LDAP_HOST`. Only local accounts work in that state. |
| Login hangs for ~30s then fails | `enabled = true` but `LDAP_HOST` is unreachable. Fix the host or disable LDAP. |
| Lost the admin password | Delete no data: use `python -c "import users; users.set_password(users.get_user('admin')['id'], 'new-password')"` from the repo root with the venv active. |
| Form shows raw ids like `slow_result` | The taxonomy changed but the process is still running the old one. Restart. |
| `Database already up to date` but a column is missing | The database predates `schema_migrations`. Steps are individually idempotent, so re-running is safe; check `migrate_db.py --status`. |
| App exits with `config.ini enables HTTPS but these files are missing` | The paths in `[HTTPS]` are wrong, or the certificate was never generated. Fix the paths or run `scripts/make_self_signed_cert.py`. |
| The browser warns about the certificate | It's self-signed. Expected for a test pair; use a CA-issued certificate for real use. |
| `Port 5000 is already in use` | An earlier run is still alive. On Windows: `Get-NetTCPConnection -State Listen -LocalPort 5000`. |

More detail lives in [docs/](docs/README.md): the schema and every migration in
[docs/database.md](docs/database.md), the login flow in
[docs/authentication.md](docs/authentication.md), the taxonomy in
[docs/configuration.md](docs/configuration.md).
