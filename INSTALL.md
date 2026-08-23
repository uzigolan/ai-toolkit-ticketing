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
| `[SERVER]` | `http_enabled`, `http_port`, `bind` | The plain listener, and the interface both listeners bind to |
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
as below. The systemd unit runs waitress via `serve.py`, which does not
terminate TLS, so a proxy is the expected setup there.

## Run as a Linux service

Tested on RHEL/Rocky 8+ and Debian/Ubuntu with systemd. The result: waitress
serving on the address from `config.ini`, a SQLite database built by
`migrate_db.py`, restarts on failure and starts at boot.

Paths below assume `/opt/rad-ticketing`; change them in the unit file too if
you pick another.

**1. Prerequisites**

```bash
# RHEL/Rocky
sudo dnf install -y python3 python3-pip git sqlite
# Debian/Ubuntu
sudo apt install -y python3 python3-venv python3-pip git sqlite3
```

**2. Create a service account and install the code**

```bash
sudo useradd --system --home /opt/rad-ticketing --shell /sbin/nologin radticketing
sudo mkdir -p /opt/rad-ticketing
sudo git clone https://github.com/uzigolan/ai-toolkit-ticketing.git /opt/rad-ticketing
sudo chown -R radticketing:radticketing /opt/rad-ticketing
```

Copying an existing checkout instead works too — just leave the developer's
`.venv`, `.secret_key` and `tickets.sqlite` behind:

```bash
sudo rsync -a --exclude .git --exclude .venv --exclude '*.sqlite' \
    --exclude .secret_key ./ /opt/rad-ticketing/
sudo chown -R radticketing:radticketing /opt/rad-ticketing
```

**3. Build the virtual environment**

```bash
sudo -u radticketing python3 -m venv /opt/rad-ticketing/.venv
sudo -u radticketing /opt/rad-ticketing/.venv/bin/pip install \
    -r /opt/rad-ticketing/requirements.txt
```

`waitress` is in `requirements.txt`; the service serves through it.

**4. Write `config.ini`**

Ports, the bind address and LDAP all come from `config.ini` in the install
root, which `serve.py` reads — no port lives in the unit file or the
environment.

```bash
sudo -u radticketing cp /opt/rad-ticketing/config.ini.example /opt/rad-ticketing/config.ini
sudo -u radticketing vi /opt/rad-ticketing/config.ini
sudo chmod 600 /opt/rad-ticketing/config.ini
```

A typical server file:

```ini
[SERVER]
http_enabled = true
http_port = 5000
bind = 0.0.0.0

[HTTPS]
enabled = false

[LDAP]
enabled = true
LDAP_HOST = ldap.yourcompany.com
LDAP_PORT = 389
LDAP_PEOPLE_DN = ou=people,dc=yourcompany,dc=com
LDAP_BASE_DN = dc=yourcompany,dc=com
```

`bind = 0.0.0.0` answers on every address of the host, on whichever ports are
set above; use `127.0.0.1` when a proxy on the same machine fronts it. Open the
port to match:

```bash
sudo firewall-cmd --add-port=5000/tcp --permanent && sudo firewall-cmd --reload
# Debian/Ubuntu with ufw
sudo ufw allow 5000/tcp
```

`serve.py` refuses to start with `[HTTPS] enabled = true`, because waitress
does not terminate TLS — put a proxy in front instead, as in
[Behind a reverse proxy](#behind-a-reverse-proxy).

**5. Write the environment file**

Secrets and file locations only; everything else is `config.ini`.

```bash
sudo tee /etc/sysconfig/rad-ticketing >/dev/null <<EOF
TICKETING_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
TICKETING_DB=/opt/rad-ticketing/tickets.sqlite
TICKETING_CATEGORIES_FILE=/opt/rad-ticketing/categories.yml
TICKETING_ADMINS=uzi
TICKETING_BOOTSTRAP_ADMIN=admin
EOF
sudo chmod 600 /etc/sysconfig/rad-ticketing
sudo chown root:root /etc/sysconfig/rad-ticketing
```

Keep `TICKETING_SECRET_KEY` stable — changing it logs everyone out. On
Debian/Ubuntu use `/etc/default/rad-ticketing` and point `EnvironmentFile` in
the unit at it.

**6. Build the database from scratch**

`migrate_db.py` owns the whole schema, so a fresh install needs nothing but a
run against a path that doesn't exist yet: it creates the file and applies
every step in order. Run it **as the service account**, or the database ends up
owned by root and the service can't write to it.

```bash
cd /opt/rad-ticketing
sudo -u radticketing env TICKETING_DB=/opt/rad-ticketing/tickets.sqlite \
    .venv/bin/python migrate_db.py
sudo -u radticketing env TICKETING_DB=/opt/rad-ticketing/tickets.sqlite \
    .venv/bin/python migrate_db.py --status
sudo chmod 600 /opt/rad-ticketing/tickets.sqlite
```

`--status` should show every step applied and nothing pending. No admin account
exists yet — that is created on the first app start, in the next step.

This step is optional, since the unit runs the same migration as
`ExecStartPre`, but doing it by hand surfaces a schema problem now instead of
as a failed unit.

**7. Install and start the unit**

```bash
sudo cp /opt/rad-ticketing/scripts/install/rad-ticketing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rad-ticketing
systemctl status rad-ticketing
```

`ExecStartPre` runs `migrate_db.py` on every start, so the schema is always
current and a failed migration stops the unit rather than letting the app serve
against a half-built database. `ExecStart` then runs `serve.py`, which reads
`config.ini`.

**8. First login**

The first start creates the local admin and prints its generated password to
the journal, once:

```bash
sudo journalctl -u rad-ticketing | grep 'generated password'
curl -sI http://127.0.0.1:5000/login          # expect HTTP/1.1 200 OK
```

Browse to `http://<server>:5000`, log in as `admin`, and change the password at
`/account/password`.

**Day-to-day**

```bash
sudo systemctl restart rad-ticketing     # after editing config.ini or categories.yml
sudo systemctl stop rad-ticketing
sudo journalctl -u rad-ticketing -f      # follow the log
sudo journalctl -u rad-ticketing -p err  # errors only
```

The unit runs with `ProtectSystem=full`, `PrivateTmp` and
`ReadWritePaths=/opt/rad-ticketing`, so the app can only write inside its own
directory. If you move `TICKETING_DB` elsewhere, add that path to
`ReadWritePaths` or the service fails on the first write.

## Behind a reverse proxy

The service listens on `[SERVER] bind`, `0.0.0.0` by default. Set it to
`127.0.0.1` when nginx or Apache terminates TLS on the same host. LDAP
passwords cross the wire at login, so **do not serve this over plain HTTP**
beyond localhost or a trusted network.

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
| Unit fails with `attempt to write a readonly database` | `tickets.sqlite` was created by root. `sudo chown radticketing:radticketing /opt/rad-ticketing/tickets.sqlite`. |
| Unit exits 1 with `serve.py runs waitress, which does not terminate TLS` | `config.ini` has `[HTTPS] enabled = true`. Set it to false and terminate TLS at the proxy. |
| The service ignores the port you set | `config.ini` is read from the working directory. Confirm it sits in `/opt/rad-ticketing` and `WorkingDirectory` matches. |
| Unit fails with `status=203/EXEC` | The venv path in the unit is wrong, or `.venv/bin/python` isn't executable by `radticketing`. |
| Answers on `127.0.0.1` but not from other hosts | `bind = 127.0.0.1`, or the port is closed. Check with `ss -lntp \| grep 5000` and open the firewall. |
| nginx returns 502 with SELinux enforcing | `sudo setsebool -P httpd_can_network_connect 1`. |

More detail lives in [docs/](docs/README.md): the schema and every migration in
[docs/database.md](docs/database.md), the login flow in
[docs/authentication.md](docs/authentication.md), the taxonomy in
[docs/configuration.md](docs/configuration.md).
