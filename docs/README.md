# Documentation

**Contents:** [What this app is](#what-this-app-is) ·
[The documents](#the-documents) ·
[Where to start](#where-to-start) ·
[Source map](#source-map) ·
[Conventions](#conventions)

## What this app is

RAD AI Ticketing Center is a small, self-hosted Flask app for collecting
structured feedback about the RAD AI toolkits. An employee files a ticket
describing a bad answer, an admin triages it and hands it back with a fix and a
corrected answer, the submitter verifies it, and the confirmed ticket can be
exported as a regression eval case.

Everything is deliberately plain: SQLite with no ORM, server-rendered Jinja
templates, no build step, no JavaScript framework. The whole app is roughly
seven Python modules and seven templates.

## The documents

| Document | What it covers |
| --- | --- |
| [architecture.md](architecture.md) | Module layout, request flow, why each piece exists |
| [workflow.md](workflow.md) | Ticket lifecycle, statuses, who may do what |
| [configuration.md](configuration.md) | `categories.yml` taxonomy and `config.ini` reference |
| [database.md](database.md) | Schema, every migration, dedup signature |
| [authentication.md](authentication.md) | Local accounts, LDAP, roles, sessions, CSRF |
| [eval-cases.md](eval-cases.md) | Promoting a ticket into a regression case |

Installing and running is in [../INSTALL.md](../INSTALL.md); the rationale
behind the design is in [../README.md](../README.md).

## Where to start

- **Filing or triaging tickets** → [workflow.md](workflow.md)
- **Running it for a team** → [../INSTALL.md](../INSTALL.md), then
  [authentication.md](authentication.md)
- **Changing the form's vocabulary** → [configuration.md](configuration.md)
- **Changing the code** → [architecture.md](architecture.md), then
  [database.md](database.md)

## Source map

```
app.py            Flask routes, form handling, permissions, the dev server
db.py             Ticket and comment rows: read and write only, no DDL
users.py          User rows: local + LDAP accounts, roles, statuses
ldap_auth.py      A single bind against the directory
migrate_db.py     Every CREATE / ALTER / INDEX, as numbered migrations
taxonomy.py       Loads and validates categories.yml
version.py        APP_NAME and APP_VERSION, used in the UI and in exports
categories.yml    The form vocabulary: toolkits, categories, severities, ...
config.ini        Ports, TLS and LDAP (not in git; see config.ini.example)
templates/        Jinja templates, all extending base.html
static/style.css  All the styling; the CDN only supplies Bootstrap's grid
scripts/          systemd unit and the self-signed certificate helper
exports/          Eval-case JSON written by "Promote to eval case"
```

## Conventions

- **One owner per concern.** Only `migrate_db.py` changes the schema, only
  `taxonomy.py` reads the YAML, only `users.py` touches the `users` table.
- **Timestamps are UTC** in the database, in the format `YYYY-MM-DD HH:MM:SS`,
  and are rendered into the reader's own timezone in the browser.
- **Multi-select fields are stored as comma-joined ids** in a single `TEXT`
  column — no join tables, because nothing queries across them.
- **Ids are the contract.** Labels in `categories.yml` are free to change; ids
  end up in the database and in exported eval cases and should not.
- **Validation is allowlist-shaped.** Anything arriving from a form is checked
  against the loaded vocabulary or a fixed tuple before it reaches SQL.
