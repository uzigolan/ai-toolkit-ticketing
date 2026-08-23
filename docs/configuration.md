# Configuration

**Contents:** [What lives where](#what-lives-where) ·
[Environment variables](#environment-variables) ·
[config.ini](#configini) ·
[categories.yml](#categoriesyml) ·
[Toolkits](#toolkits) ·
[Categories](#categories) ·
[Facets](#facets) ·
[Severities](#severities) ·
[Resolutions](#resolutions) ·
[Changing the taxonomy safely](#changing-the-taxonomy-safely) ·
[Validation and failure modes](#validation-and-failure-modes)

## What lives where

| Concern | Where |
| --- | --- |
| Secrets, file locations, bootstrap admin | Environment variables |
| Ports, TLS material, LDAP connection | `config.ini` (not in git) |
| Everything the submit form offers | `categories.yml` |
| Statuses and permissions | Code — they *are* the workflow |

Nothing is required for a local, local-accounts-only install: with no
`config.ini` and no `categories.yml` the app runs on built-in defaults.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `TICKETING_SECRET_KEY` | `change-me-in-config` | Signs session cookies. **Set this in production**; changing it logs everybody out. |
| `TICKETING_DB` | `tickets.sqlite` | Database file location. Read by `migrate_db.db_path()`, so the app and the migration CLI always agree. |
| `TICKETING_CATEGORIES_FILE` | `categories.yml` | Taxonomy file location |
| `TICKETING_ADMINS` | *(empty)* | Comma-separated usernames seeded as admins the first time each is seen |
| `TICKETING_BOOTSTRAP_ADMIN` | `admin` | Username of the first local admin |
| `TICKETING_BOOTSTRAP_PASSWORD` | *(generated)* | Password of the first local admin, printed once to the log if generated |
| `FLASK_RUN_PORT` | *(unset)* | Overrides the port when only one listener is enabled |

## config.ini

Copy `config.ini.example` and edit. It is git-ignored, because it holds the
LDAP service-account password and points at private keys.

### `[SERVER]`

| Key | Default | Meaning |
| --- | --- | --- |
| `http_enabled` | `true` | Serve plain HTTP |
| `http_port` | `5000` | Port for it |
| `bind` | `0.0.0.0` | Interface both listeners bind to. `0.0.0.0` is every address of the host; `127.0.0.1` is that machine only |

### `[HTTPS]`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Terminate TLS in the app itself |
| `port` | `5443` | Port for it |
| `ssl_cert` | *(empty)* | Certificate file, PEM |
| `ssl_key` | *(empty)* | Private key file, PEM |

Put the certificate and key under `https/` — everything in that directory is
git-ignored, so a private key cannot be committed by accident. Generate a
throwaway pair for testing with:

```powershell
.\.venv\Scripts\python.exe scripts\make_self_signed_cert.py
```

If `enabled = true` and either file is missing, the app **refuses to start**
and names the missing files. Quietly falling back to plaintext would put login
passwords on the wire without anybody noticing.

With both sections enabled, one process answers on both ports — see
[architecture.md](architecture.md#serving-http-and-https).

### `[LDAP]`

| Key | Meaning |
| --- | --- |
| `enabled` | Master switch. LDAP is also off whenever `LDAP_HOST` is empty, whatever this says. |
| `LDAP_HOST` / `LDAP_PORT` / `LDAP_USE_SSL` | Where the directory is |
| `LDAP_PEOPLE_DN` | Container holding user entries; used for the direct-bind attempts |
| `LDAP_BASE_DN` | Search base for the admin-bind fallback |
| `LDAP_ADMIN_DN` / `LDAP_ADMIN_PASSWORD` | Service account, needed when direct-bind DN patterns don't match (typical for AD with `sAMAccountName` logins) |

Details of how these are used are in
[authentication.md](authentication.md#ldap-accounts).

## categories.yml

The whole vocabulary of the submit form. Read once at startup, so **restart
after editing** — although the development server watches this file and
reloads by itself.

Every section is a list of entries. An entry is either a bare string, in which
case the id and label are the same, or a mapping:

```yaml
categories:
  - id: wrong_result          # stored in the database
    label: Wrong result       # shown on the form
```

Some sections accept `default: true`, which preselects that entry on a fresh
form.

## Toolkits

Which AI toolkit the ticket is about — the first thing a submitter picks, and
the scope for dedup.

```yaml
toolkits:
  - id: rad-agent-toolkit
    label: rad-agent-toolkit
    default: true
  - id: radview-ai-toolkit
    label: radview-ai-toolkit
  - id: other
    label: Something else / not listed yet
```

Adding a toolkit is one entry and a restart — no code change and no migration.
Keep an "other" entry so a toolkit that doesn't exist yet never blocks
somebody from filing.

## Categories

What went wrong. Multi-select and part of the dedup signature.

```yaml
categories:
  - id: wrong_result
    label: Wrong result
  - id: bad_format
    label: Right, but wrong format
  - id: slow_result
    label: Right, but too slow
  - id: many_retries
    label: Took too many retries
```

## Facets

Four multi-select lists that describe the context rather than the failure.
None of them affect the dedup signature.

| Section | Question it answers | `default:` |
| --- | --- | --- |
| `rad_families` | Which device families the report covers | no |
| `knowledge_scope` | RAD-specific knowledge, general market knowledge, or both | yes |
| `knowledge_sources` | Which reference material the answer should have come from | no |
| `operations` | What the agent was actually doing | no |

`rad_families` is usually written as bare strings (`ETX`, `SecFlow`, …). The
shipped `knowledge_sources` covers manual, datasheet, release notes, CLI, MIB,
MEA/debug, skills and vendor docs; `operations` runs from `lookup` through
`design` and `device_read` to `device_write`, with risk growing down the list.

## Severities

```yaml
severities:
  - id: low
    label: Low
  - id: normal
    label: Normal
  - id: high
    label: High — blocking my work
```

The **order matters**: it is the ranking used when a list is sorted by
severity, most severe first. `normal` is the default if present, otherwise the
first entry.

## Resolutions

What an admin can hand a ticket back as. This is the only place the workflow is
configurable.

```yaml
resolutions:
  - id: fixed
    label: Fixed — ready for verification
    status: ready_for_verification
    requires_version: true
    requires_answer: true
  - id: known_issue
    label: Known issue — not fixed for now
    status: known_issue
    requires_note: true
```

| Key | Effect |
| --- | --- |
| `status` | The status the ticket moves into. Defaults to the entry's own id; use one of the statuses in [workflow.md](workflow.md#statuses), since nothing validates it. |
| `requires_version` | The resolve form demands the `rad agent show versions` output of the fixed build |
| `requires_answer` | It demands the corrected answer from re-running the submitter's prompt |
| `requires_note` | It demands a written explanation |

Those flags are what make "fixed" mean something checkable rather than a claim.

## Changing the taxonomy safely

- **Labels are free.** Change wording whenever you like.
- **Ids are the contract.** They land in the database and in exported eval
  cases. Renaming one orphans the tickets filed under it — they still display,
  just by their raw id.
- **Removing an entry** doesn't delete data either; existing tickets keep the
  value and show the raw id. If you want it gone, edit the affected tickets
  first.
- **Adding an entry** is always safe.
- Changing a `default:` only affects new forms, never stored tickets.

## Validation and failure modes

| Situation | What happens |
| --- | --- |
| `categories.yml` missing | Built-in defaults are used and the app runs |
| The file is not valid YAML, or isn't a mapping at the top level | Startup fails, quoting the parser error |
| `categories` missing or empty | Startup fails — it's the one required section |
| An entry lacks an `id`, isn't a string or mapping, or duplicates an id | Startup fails, naming the section and index, e.g. `categories[2] is missing an 'id'` |
| Any other section missing or empty | That section falls back to its built-in defaults |
| The file exists but PyYAML isn't installed | Startup fails telling you to install requirements |
| A form submits an id not in the loaded vocabulary | The submission is rejected, not stored |

The asymmetry is deliberate: no file at all is a fresh install, but a broken
file is a typo — and silently ignoring it would leave everybody filing tickets
against a vocabulary nobody intended.

Check what was loaded in the startup log:

```
INFO:app:Loaded ticket taxonomy from categories.yml: 5 toolkits, 4 categories.
```
