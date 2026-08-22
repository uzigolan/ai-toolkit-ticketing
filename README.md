# RAD AI Ticketing Center

A small, self-hosted Flask app so any company employee can submit a
structured ticket about any of the RAD AI toolkits — a wrong result, a right
result that took too long, or one that needed a pile of retries — logged in
with their normal company LDAP credentials or a local account. No separate
password to manage for people who already have a directory login.

It serves every toolkit, not just one: `rad-agent-toolkit`,
`radview-ai-toolkit`, `pikachu-ai-toolkit`, `synergy-ccm-mcp`, and whatever
comes next. Choosing the toolkit is the first thing a submitter does, and the
list lives in `categories.yml` — adding one is a line of YAML and a restart,
with no code change and no migration.

**Contents:** [Why this shape](#why-this-shape) · [Run it](#run-it) ·
[Documentation](#documentation) ·
[What's deliberately left for you to decide](#whats-deliberately-left-for-you-to-decide) ·
[Deploying for real](#deploying-for-real)

## Documentation

Installing and running: [INSTALL.md](INSTALL.md). Everything else is in
[docs/](docs/README.md):

| Document | What it covers |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Module layout, request flow, how the server is wired |
| [docs/workflow.md](docs/workflow.md) | Ticket lifecycle, statuses, who may do what |
| [docs/configuration.md](docs/configuration.md) | `categories.yml` and `config.ini` reference |
| [docs/database.md](docs/database.md) | Schema, migrations, the dedup signature |
| [docs/authentication.md](docs/authentication.md) | Local accounts, LDAP, roles, sessions |
| [docs/eval-cases.md](docs/eval-cases.md) | Turning a verified ticket into a regression case |

## Why this shape

- **One app, many toolkits**: a ticket names the toolkit it's about, every
  list can be filtered by it, and it scopes the dedup — the same prompt asked
  of two different toolkits is two problems, not a duplicate. Toolkits that
  don't exist yet are covered by the "not listed yet" option, so nobody is
  blocked from filing.

- **Local accounts and LDAP together** (`users.py` + `ldap_auth.py`): this
  follows the PKI repo's model. There is one `users` table, and every row
  carries an `auth_source` of `local` or `ldap`. Local rows hold a werkzeug
  password hash and are checked in-process; LDAP rows hold an empty hash and
  are checked by a live bind against the directory, the row existing only to
  carry role, status and profile. Login looks the user up first and verifies
  against whatever that row names; an unknown username falls through to LDAP
  and, on a successful bind, gets its row provisioned automatically. So the
  app runs on local accounts alone (no directory at all), on LDAP alone, or
  on both — contractors and service accounts get local logins while employees
  keep using their normal company credentials.
- **Roles live in the database, not in an env var**: `TICKETING_ADMINS` only
  seeds the admin role the first time a username is seen. After that, promote
  and demote from `/admin/users`. The app refuses to demote, disable or delete
  the last active admin, or to let an admin do any of those to themselves.
- **Structured intake, not free text** (`templates/new_ticket.html`): the form
  asks for four things — the toolkit, what went wrong, the exact prompt, and
  the whole pasted chat. Everything else (title, description, expected vs.
  actual, severity, versions) sits in a collapsed *Advanced details* section,
  because a report that arrives at all beats a perfect one that nobody fills
  in. If the title is left blank it's derived from the first substantial line
  of the prompt. What went wrong is multi-select and feeds the dedup
  signature. The full form is described in
  [docs/workflow.md](docs/workflow.md#filing-a-ticket).
- **The taxonomy is config, not code** (`categories.yml` + `taxonomy.py`): the
  toolkits, categories, device families, facets, severities and resolutions all
  come from a YAML file read at startup, so changing the vocabulary is an edit
  and a restart, not a code change. Submissions are validated against whatever
  was loaded — an unknown id is rejected rather than stored. If the file is
  absent, built-in defaults keep the app running; if it's present but
  malformed, startup fails with a message naming the offending entry, because
  silently ignoring a typo would leave people filing tickets against a taxonomy
  nobody intended. Keep the `id` values stable — they're what land in the
  database and in exported eval cases. Reference:
  [docs/configuration.md](docs/configuration.md).
- **One place owns the schema** (`migrate_db.py`): every `CREATE`, `ALTER` and
  index lives in a numbered migration, the way the PKI repo's `migrate_db.py`
  does it. `db.py` and `users.py` only read and write rows. Each step is
  recorded in a `schema_migrations` table, so re-running is a no-op and you can
  see what a given database has had done to it; each step is also individually
  idempotent, so a database built before this existed gets adopted rather than
  broken. Run `python migrate_db.py --status` to see what's applied, and
  `python migrate_db.py` to bring one up to date. The app runs it at startup
  too, so a fresh clone just works. `TICKETING_DB` overrides the file location.
- **Which data was involved, and what was being done**: alongside the RAD
  family, a ticket records the reference material the answer should have come
  from (user manual, datasheet, release notes, CLI reference, MIBs, MEA/debug,
  skills, vendor docs) and what the agent was actually doing (knowledge lookup,
  device read, device change). Both are multi-select, because the real shapes
  are combinations — "the CLI reference disagreed with the manual", or "a
  question that ended in a config change". Together they point triage at the
  source that needs fixing and separate the reports that only read from the
  ones that touched live kit. Both vocabularies live in `categories.yml`.
  They're deliberately *not* part of the dedup signature: two people hitting one
  bug shouldn't become two tickets because one of them ticked an extra box.
- **The prompt is a field of its own**: the submit form asks for the exact
  prompt separately from the pasted chat. That separation is what makes the
  round trip work — when an admin closes a ticket, the close form shows that
  prompt with a copy button, they re-run it against the fixed build, and paste
  what it says now. The ticket then carries the original prompt, the version
  that fixed it, and the corrected answer, and the exported eval case is a
  ready-made `prompt` / `expected_answer` pair rather than a prose description
  of a bug. It's also the strongest dedup signal: two people asking the same
  thing and hitting the same wall collapse onto one signature.
- **Closing states an outcome, and only the submitter can close**: an admin
  can't close a ticket at all — they pick a resolution (`fixed` /
  `known_issue`, configurable in `categories.yml`) and hand the ticket back. An
  outcome marked `requires_version: true` demands the `rad agent show versions`
  output of the build carrying the fix; `requires_answer: true` demands the
  corrected answer from re-running the prompt on it. Then the person who filed
  it decides: verify, and the ticket is settled, or reopen. That's the point:
  "fixed" is pinned to a version the submitter can move to and an answer they
  can check, rather than a claim, and it's confirmed by somebody other than the
  fixer. Reopening clears all of it so a stale outcome can't linger on an open
  ticket. Full lifecycle: [docs/workflow.md](docs/workflow.md).
- **Everyone sees everything, owners can add** (`ticket_detail`): any logged-in
  user can read every ticket — seeing what's already reported is what stops the
  same issue being filed five times. Only the submitter (and admins) can add
  follow-ups to a given ticket, and the submitter's ability to do so ends when
  they verify it. Admins can always add, including to reopen the conversation.
- **Dedup by failure signature** (`db.compute_signature`): near-identical
  reports collapse onto the same signature and get auto-linked as
  duplicates, so ten people hitting the same bug produce one triage item,
  not ten. This mirrors — at a much smaller scale — the Tier-1 signature
  dedup already described in rad-agent-toolkit's `docs/feedback-loop.md`
  and implemented in `scripts/feedback_collector.py`. If you want one
  dedup engine instead of two, swap this function out for a call into
  that service.
- **The actual "teach the repo" step** (`db.export_as_eval_case` /
  the *Promote to eval case* button on a ticket): once you've triaged a
  ticket and confirmed it's real, promoting it writes a JSON stub into
  `exports/` shaped like a case in the toolkit's `tests/evals/cases/`.
  Copy that file into the toolkit repo (or wire a small script to do it
  automatically) and it becomes a permanent regression check — the toolkit
  can't silently regress on something a real employee hit. The case names the
  toolkit it belongs to and the ticketing version that produced it. Details:
  [docs/eval-cases.md](docs/eval-cases.md).

## Run it

On Windows, from the repo root:

```powershell
.\start.ps1
```

It creates `.venv` on first run, installs requirements when they change,
generates a persistent session key in `.secret_key`, and runs the app in the
foreground — Ctrl+C stops it. Useful switches: `-Port 8080`, `-SkipInstall`,
and `-Production` (serves via waitress instead of the debug server).

Manually, or on Linux/macOS:

```bash
pip install -r requirements.txt
export TICKETING_SECRET_KEY="something-random"
python app.py
```

The first start creates a local admin (`admin` by default) and prints a
generated password to the log — change it at `/account/password`. Set
`TICKETING_BOOTSTRAP_ADMIN` / `TICKETING_BOOTSTRAP_PASSWORD` to choose them
yourself. From there, `/admin/users` creates further local accounts.

To add company LDAP on top, copy `config.ini.example` to `config.ini`, fill in
your directory details and set `enabled = true`. With that in place, employees
log in with their normal credentials and their account is created on first
successful bind; local accounts keep working alongside them. Optionally set
`TICKETING_ADMINS="alice,bob"` to seed those usernames as admins the first time
they log in.

Browse to `http://localhost:5000`. Admins see the `/admin` triage queue and
`/admin/users`; everyone else only sees `/tickets/mine`.

To serve TLS from the app itself instead, put a certificate and key under
`https/` and enable `[HTTPS]` in `config.ini` — see
[INSTALL.md](INSTALL.md#serving-https). With `[SERVER] http_enabled` left on,
one process answers on both ports.

## What's deliberately left for you to decide

- **Admin list**: `TICKETING_ADMINS` only seeds the role on first sight; roles
  then live in the database. If your LDAP groups map to something like
  `cn=rad-toolkit-admins,...`, add a group-membership check in `ldap_auth.py`
  and sync the role on each login — more scalable than an allowlist.
- **Where tickets ultimately live**: SQLite is fine for a small team; if
  ticket volume grows or you want it unified with the feedback traces
  already flowing into `feedback_collector.py`, point `db.py` at the same
  Postgres/whatever store that service uses.
- **Auto-promotion vs. human triage**: this starter always requires a
  human to click "Promote to eval case" before anything reaches the
  toolkit repo. That's deliberate — a wrong or malicious ticket becoming
  a permanent eval case unreviewed would be worse than a slow queue. If
  you trust the signature dedup enough, you could auto-promote anything
  with 3+ duplicate reports.
- **Notifying submitters when fixed**: not wired up yet. Cheapest version
  is emailing `submitter_email` when a ticket's status flips to
  `ready_for_verification`; `smtplib` against your company mail relay is
  enough, no need for a queue.

## Deploying for real

This is a starter, not production-hardened:
- Put it behind HTTPS — either terminate TLS in the app via `[HTTPS]` in
  `config.ini`, or run nginx in front. LDAP credentials go over the wire on
  login.
- Run with a real WSGI server (gunicorn/waitress), not `app.run(debug=True)`.
- Move `TICKETING_SECRET_KEY` out of the default and out of shell history
  (a secrets manager or `.env` file not committed to git).
