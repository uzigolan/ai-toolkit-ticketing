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

Tested on Rocky/RHEL 8+ with systemd. The service runs entirely out of a git
clone in the `rocky` user's home — its own `.venv`, its own `config.ini`, its
own database — and never touches the OS python beyond creating that venv. So an
update is just:

```bash
cd ~/ai-toolkit-ticketing && git pull && sudo systemctl restart rad-ticketing
```

Paths below assume `/home/rocky/ai-toolkit-ticketing` and the user `rocky`,
which is what the shipped unit file expects. Using another home or account
means editing the five paths and the `User=`/`Group=` lines in
`scripts/install/rad-ticketing.service`.

**1. Prerequisites**

```bash
sudo dnf install -y python3 python3-pip git sqlite
```

**2. Clone the repo**

As `rocky`, not with sudo — everything must stay owned by the service user.

```bash
cd ~
git clone https://github.com/uzigolan/ai-toolkit-ticketing.git
cd ~/ai-toolkit-ticketing
```

**3. Build the virtual environment inside the clone**

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

The unit calls `.venv/bin/python` directly, so this venv — not the system
interpreter — is what serves the app. `waitress` comes from
`requirements.txt`, so a `git pull` that bumps a dependency only needs
`.venv/bin/pip install -r requirements.txt` afterwards.

**4. Write `config.ini`**

Ports, the bind address and LDAP all come from `config.ini` in the clone, which
`serve.py` reads — no port lives in the unit file or the environment. It is
git-ignored, so `git pull` never overwrites it.

```bash
cp config.ini.example config.ini
vi config.ini
chmod 600 config.ini
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
```

`serve.py` refuses to start with `[HTTPS] enabled = true`, because waitress
does not terminate TLS — put a proxy in front instead, as in
[Behind a reverse proxy](#behind-a-reverse-proxy).

**5. Write `.env` in the clone**

Secrets and file locations, read by the unit's `EnvironmentFile`. Also
git-ignored.

```bash
cat > ~/ai-toolkit-ticketing/.env <<EOF
TICKETING_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
TICKETING_DB=/home/rocky/ai-toolkit-ticketing/tickets.sqlite
TICKETING_CATEGORIES_FILE=/home/rocky/ai-toolkit-ticketing/categories.yml
TICKETING_ADMINS=uzi
TICKETING_BOOTSTRAP_ADMIN=admin
EOF
chmod 600 ~/ai-toolkit-ticketing/.env
```

systemd parses this file itself, so write plain `KEY=value` lines: no `export`,
no `$(...)`, no quotes unless the value contains them. Keep
`TICKETING_SECRET_KEY` stable — changing it logs everyone out.

**6. Build the database from scratch**

`migrate_db.py` owns the whole schema, so a fresh install needs nothing but a
run against a path that doesn't exist yet: it creates the file and applies
every step in order. Run it as `rocky`, never with sudo, or the database ends
up owned by root and the service can't write to it.

```bash
cd ~/ai-toolkit-ticketing
set -a; . ./.env; set +a
.venv/bin/python migrate_db.py
.venv/bin/python migrate_db.py --status
chmod 600 tickets.sqlite
```

`--status` should show every step applied and nothing pending. No admin account
exists yet — that is created on the first app start, in the next step.

This step is optional, since the unit runs the same migration as
`ExecStartPre`, but doing it by hand surfaces a schema problem now instead of
as a failed unit.

**7. Install and start the unit**

The unit file is the only thing that leaves the clone.

```bash
sudo cp ~/ai-toolkit-ticketing/scripts/install/rad-ticketing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rad-ticketing
systemctl status rad-ticketing
```

`ExecStartPre` runs `migrate_db.py` on every start, so the schema is always
current and a failed migration stops the unit rather than letting the app serve
against a half-built database. `ExecStart` then runs `serve.py`, which reads
`config.ini`.

If the unit file itself changes in a later `git pull`, copy it over again and
`sudo systemctl daemon-reload`.

With SELinux enforcing, systemd may refuse to execute an interpreter that lives
under `/home`. If `systemctl status` shows a permission denial and
`sudo ausearch -m avc -ts recent` confirms it, label the venv and relabel:

```bash
sudo dnf install -y policycoreutils-python-utils
sudo semanage fcontext -a -t bin_t '/home/rocky/ai-toolkit-ticketing/.venv/bin(/.*)?'
sudo restorecon -Rv /home/rocky/ai-toolkit-ticketing
```

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

Updating:

```bash
cd ~/ai-toolkit-ticketing
git pull
.venv/bin/pip install -r requirements.txt   # only if requirements.txt changed
sudo systemctl restart rad-ticketing
```

`config.ini`, `.env` and `tickets.sqlite` are git-ignored, so a pull leaves them
alone, and migrations run on restart.

The unit runs with `ProtectSystem=full`, `PrivateTmp` and
`ReadWritePaths=/home/rocky/ai-toolkit-ticketing`, so the app can only write
inside the clone. If you move `TICKETING_DB` elsewhere, add that path to
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

The service runs from the clone, so an upgrade is a pull and a restart:

```bash
cd ~/ai-toolkit-ticketing
cp tickets.sqlite ~/backups/tickets-$(date +%F).sqlite
git pull
.venv/bin/pip install -r requirements.txt   # only if requirements.txt changed
sudo systemctl restart rad-ticketing
```

`config.ini`, `.env` and `tickets.sqlite` are git-ignored, so the pull leaves
them alone. Migrations run on start and are idempotent, so an upgrade that adds
no schema changes is a no-op. Check what a database has had applied with:

```bash
cd ~/ai-toolkit-ticketing && .venv/bin/python migrate_db.py --status
```

## Backup and restore

Everything lives in one SQLite file plus two config files:

```bash
cd ~/ai-toolkit-ticketing
sqlite3 tickets.sqlite ".backup '$HOME/backups/tickets.sqlite'"
cp config.ini categories.yml .env ~/backups/
```

Use `.backup` rather than `cp` while the service is running — it takes a
consistent snapshot instead of a possibly torn file. Restore by stopping the
service, putting the files back and starting it again.

## Uninstall

```bash
sudo systemctl disable --now rad-ticketing
sudo rm /etc/systemd/system/rad-ticketing.service
sudo systemctl daemon-reload
rm -rf ~/ai-toolkit-ticketing          # this deletes the ticket database
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
| Unit fails with `attempt to write a readonly database` | `tickets.sqlite` was created by root. `sudo chown rocky:rocky ~/ai-toolkit-ticketing/tickets.sqlite`. |
| Unit exits 1 with `serve.py runs waitress, which does not terminate TLS` | `config.ini` has `[HTTPS] enabled = true`. Set it to false and terminate TLS at the proxy. |
| The service ignores the port you set | `config.ini` is read from the working directory. Confirm it sits in `/home/rocky/ai-toolkit-ticketing` and `WorkingDirectory` matches. |
| Unit fails with `status=203/EXEC` | `.venv` was never built, or was built somewhere other than the clone. Re-run the venv step and check `.venv/bin/python` exists. |
| Unit fails with `status=200/CHDIR` or permission denied under `/home` | The clone is not at the path in the unit, or `ProtectHome` is back to `yes`. |
| `.env` values are ignored | systemd needs plain `KEY=value` lines — no `export`, no command substitution. |
| Answers on `127.0.0.1` but not from other hosts | `bind = 127.0.0.1`, or the port is closed. Check with `ss -lntp \| grep 5000` and open the firewall. |
| nginx returns 502 with SELinux enforcing | `sudo setsebool -P httpd_can_network_connect 1`. |

More detail lives in [docs/](docs/README.md): the schema and every migration in
[docs/database.md](docs/database.md), the login flow in
[docs/authentication.md](docs/authentication.md), the taxonomy in
[docs/configuration.md](docs/configuration.md).
