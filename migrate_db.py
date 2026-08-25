"""
Schema owner for the ticketing app, in the same spirit as the PKI repo's
migrate_db.py: every CREATE, ALTER and INDEX lives here and nowhere else.
db.py and users.py only read and write rows -- they never touch the schema.

    python migrate_db.py            # bring the database up to date
    python migrate_db.py --status   # show what's applied and what's pending

Each step is recorded in `schema_migrations`, so re-running is a no-op and you
can see what a given database has had done to it. The steps are also written to
be individually idempotent (CREATE IF NOT EXISTS, column checks before ALTER),
so a database built before this file existed -- or one restored from a backup
mid-history -- is adopted rather than broken.
"""
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone

DEFAULT_DB_PATH = "tickets.sqlite"


def db_path() -> str:
    """The one place the database location is decided."""
    return os.environ.get("TICKETING_DB") or DEFAULT_DB_PATH


# --- helpers -------------------------------------------------------------

def _columns(cur, table: str) -> list:
    return [row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(cur, table: str) -> bool:
    row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _add_column(cur, table: str, column: str, decl: str):
    if _table_exists(cur, table) and column not in _columns(cur, table):
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _rename_column(cur, table: str, old: str, new: str):
    if not _table_exists(cur, table):
        return
    cols = _columns(cur, table)
    if old in cols and new not in cols:
        cur.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")


# --- migrations ----------------------------------------------------------
# Append new steps to the end; never edit or renumber an applied one.

def _0001_tickets(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signature TEXT NOT NULL,
            category TEXT NOT NULL,
            rad_family TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            expected_behavior TEXT,
            actual_behavior TEXT,
            transcript TEXT,
            toolkit_version TEXT,
            severity TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'new',
            duplicate_of INTEGER,
            submitter_username TEXT NOT NULL,
            submitter_email TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_signature ON tickets(signature)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")


def _0002_users(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user',
            email TEXT,
            display_name TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            auth_source TEXT NOT NULL DEFAULT 'local',
            created_at TEXT NOT NULL,
            last_login TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_auth_source ON users(auth_source)")


def _0003_ticket_comments(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            author_username TEXT NOT NULL,
            author_display TEXT,
            body TEXT NOT NULL,
            is_admin_note INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_comments_ticket ON ticket_comments(ticket_id)")


def _0004_ticket_subcategory(cur):
    _add_column(cur, "tickets", "subcategory", "TEXT")


def _0005_ticket_prompt(cur):
    _add_column(cur, "tickets", "prompt", "TEXT")


def _0006_ticket_resolution(cur):
    _add_column(cur, "tickets", "resolution", "TEXT")
    _add_column(cur, "tickets", "resolution_note", "TEXT")
    _add_column(cur, "tickets", "fixed_in_versions", "TEXT")
    _add_column(cur, "tickets", "closed_by", "TEXT")
    _add_column(cur, "tickets", "closed_at", "TEXT")


def _0007_ticket_fixed_answer(cur):
    _add_column(cur, "tickets", "fixed_answer", "TEXT")


def _0008_verification_workflow(cur):
    """
    Admins stopped closing tickets: they resolve and hand back, and only the
    submitter verifies. Closing columns become resolution columns, and the two
    statuses that no longer exist are mapped onto the new flow.
    """
    _rename_column(cur, "tickets", "closed_by", "resolved_by")
    _rename_column(cur, "tickets", "closed_at", "resolved_at")
    _add_column(cur, "tickets", "resolved_by", "TEXT")
    _add_column(cur, "tickets", "resolved_at", "TEXT")
    _add_column(cur, "tickets", "verified_by", "TEXT")
    _add_column(cur, "tickets", "verified_at", "TEXT")
    _add_column(cur, "tickets", "promoted_at", "TEXT")

    # 'promoted' used to overwrite the status, losing where the ticket actually
    # was; it is a fact about export, so it moves to its own timestamp column.
    cur.execute("""
        UPDATE tickets SET promoted_at = COALESCE(promoted_at, updated_at), status = 'triaged'
        WHERE status = 'promoted'
    """)
    # An admin-closed ticket was settled, which is now what 'verified' means.
    cur.execute("""
        UPDATE tickets
        SET status = 'verified',
            verified_by = COALESCE(verified_by, submitter_username),
            verified_at = COALESCE(verified_at, resolved_at, updated_at)
        WHERE status = 'closed'
    """)
    # The old 'not_fixed' outcome is now 'known_issue'.
    cur.execute("UPDATE tickets SET resolution = 'known_issue' WHERE resolution = 'not_fixed'")


def _0009_ticket_facets(cur):
    """Which reference material and what kind of operation the report involved.
    Both are multi-select, stored as a comma-separated list of ids."""
    _add_column(cur, "tickets", "knowledge_sources", "TEXT")
    _add_column(cur, "tickets", "operations", "TEXT")


def _0010_multi_categories(cur):
    """
    Categories became multi-select and sub-categories were dropped, so the two
    old columns give way to one comma-separated list. SQLite can't relax the
    NOT NULL on `category` or drop columns cleanly in place, so the table is
    rebuilt and the rows carried across with a best-effort mapping.
    """
    if "categories" in _columns(cur, "tickets"):
        return

    cur.execute("""
        CREATE TABLE tickets_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signature TEXT NOT NULL,
            categories TEXT,
            rad_family TEXT,
            knowledge_sources TEXT,
            operations TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            expected_behavior TEXT,
            actual_behavior TEXT,
            prompt TEXT,
            transcript TEXT,
            toolkit_version TEXT,
            severity TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'new',
            duplicate_of INTEGER,
            resolution TEXT,
            resolution_note TEXT,
            fixed_in_versions TEXT,
            fixed_answer TEXT,
            resolved_by TEXT,
            resolved_at TEXT,
            verified_by TEXT,
            verified_at TEXT,
            promoted_at TEXT,
            submitter_username TEXT NOT NULL,
            submitter_email TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        INSERT INTO tickets_new
            (id, signature, categories, rad_family, knowledge_sources, operations, title,
             description, expected_behavior, actual_behavior, prompt, transcript,
             toolkit_version, severity, status, duplicate_of, resolution, resolution_note,
             fixed_in_versions, fixed_answer, resolved_by, resolved_at, verified_by,
             verified_at, promoted_at, submitter_username, submitter_email, created_at,
             updated_at)
        SELECT id, signature,
               CASE WHEN subcategory = 'slow' THEN 'slow_result' ELSE 'wrong_result' END,
               rad_family, knowledge_sources, operations, title,
               description, expected_behavior, actual_behavior, prompt, transcript,
               toolkit_version, severity, status, duplicate_of, resolution, resolution_note,
               fixed_in_versions, fixed_answer, resolved_by, resolved_at, verified_by,
               verified_at, promoted_at, submitter_username, submitter_email, created_at,
               updated_at
        FROM tickets
    """)
    cur.execute("DROP TABLE tickets")
    cur.execute("ALTER TABLE tickets_new RENAME TO tickets")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_signature ON tickets(signature)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")


def _0011_ticket_toolkit(cur):
    """The app serves several AI toolkits now. Everything filed before this
    point was about rad-agent-toolkit, so that is what those rows get."""
    _add_column(cur, "tickets", "toolkit", "TEXT")
    cur.execute(
        "UPDATE tickets SET toolkit = 'rad-agent-toolkit' "
        "WHERE toolkit IS NULL OR TRIM(toolkit) = ''"
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tickets_toolkit ON tickets(toolkit)")


def _0012_families_scope_suggestion(cur):
    """
    Device family became multi-select (one report can span ETX and SecFlow), the
    submitter can say whether RAD or market knowledge was needed, and can suggest
    the fix themselves.
    """
    _rename_column(cur, "tickets", "rad_family", "rad_families")
    _add_column(cur, "tickets", "rad_families", "TEXT")
    _add_column(cur, "tickets", "knowledge_scope", "TEXT")
    _add_column(cur, "tickets", "suggestion", "TEXT")
    # Everything filed so far was about RAD products.
    cur.execute(
        "UPDATE tickets SET knowledge_scope = 'rad' "
        "WHERE knowledge_scope IS NULL OR TRIM(knowledge_scope) = ''"
    )


def _0013_self_hosted_track(cur):
    """Second ticketing subject: self-hosted POC score/measurement reporting."""
    _add_column(cur, "tickets", "ticket_track", "TEXT")
    _add_column(cur, "tickets", "self_hosted_area", "TEXT")
    _add_column(cur, "tickets", "self_hosted_metric", "TEXT")
    _add_column(cur, "tickets", "self_hosted_result", "TEXT")
    _add_column(cur, "tickets", "self_hosted_score", "TEXT")
    _add_column(cur, "tickets", "self_hosted_target", "TEXT")
    _add_column(cur, "tickets", "self_hosted_measurement", "TEXT")
    _add_column(cur, "tickets", "self_hosted_evidence", "TEXT")
    _add_column(cur, "tickets", "self_hosted_doc_ref", "TEXT")
    cur.execute(
        "UPDATE tickets SET ticket_track = 'toolkit' "
        "WHERE ticket_track IS NULL OR TRIM(ticket_track) = ''"
    )


MIGRATIONS = [
    ("0001_tickets", "tickets table and indexes", _0001_tickets),
    ("0002_users", "local + LDAP user accounts", _0002_users),
    ("0003_ticket_comments", "follow-up comments on tickets", _0003_ticket_comments),
    ("0004_ticket_subcategory", "sub-category on tickets", _0004_ticket_subcategory),
    ("0005_ticket_prompt", "the submitter's prompt, separate from the chat", _0005_ticket_prompt),
    ("0006_ticket_resolution", "close outcome, note and fix versions", _0006_ticket_resolution),
    ("0007_ticket_fixed_answer", "corrected answer from the fixed build", _0007_ticket_fixed_answer),
    ("0008_verification_workflow", "admins resolve, submitters verify", _0008_verification_workflow),
    ("0009_ticket_facets", "knowledge sources and operation types", _0009_ticket_facets),
    ("0010_multi_categories", "multi-select categories, no sub-categories", _0010_multi_categories),
    ("0011_ticket_toolkit", "which AI toolkit the ticket is about", _0011_ticket_toolkit),
    ("0012_families_scope_suggestion", "multi-select families, knowledge scope, suggestion",
     _0012_families_scope_suggestion),
    ("0013_self_hosted_track", "self-hosted track score and measurement fields",
     _0013_self_hosted_track),
]


# --- runner --------------------------------------------------------------

def _ensure_migrations_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id TEXT PRIMARY KEY,
            description TEXT,
            applied_at TEXT NOT NULL
        )
    """)


def applied_ids(path: str = None) -> set:
    with closing(sqlite3.connect(path or db_path())) as conn:
        cur = conn.cursor()
        _ensure_migrations_table(cur)
        return {row[0] for row in cur.execute("SELECT id FROM schema_migrations").fetchall()}


def migrate(path: str = None, logger=None) -> list:
    """Apply every pending migration. Returns the ids applied this run."""
    path = path or db_path()
    done = applied_ids(path)
    applied = []
    with closing(sqlite3.connect(path)) as conn:
        cur = conn.cursor()
        _ensure_migrations_table(cur)
        for migration_id, description, step in MIGRATIONS:
            if migration_id in done:
                continue
            step(cur)
            cur.execute(
                "INSERT INTO schema_migrations (id, description, applied_at) VALUES (?,?,?)",
                (migration_id, description,
                 datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            )
            # One transaction per step: a failure leaves earlier steps applied
            # and this one not, so a re-run picks up exactly where it stopped.
            conn.commit()
            applied.append(migration_id)
            if logger:
                logger.info("Applied migration %s (%s).", migration_id, description)
    return applied


def status(path: str = None):
    path = path or db_path()
    done = applied_ids(path)
    print(f"Database: {path}")
    for migration_id, description, _ in MIGRATIONS:
        mark = "applied" if migration_id in done else "PENDING"
        print(f"  [{mark:>7}] {migration_id}  {description}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        results = migrate()
        if results:
            for migration_id in results:
                print(f"applied {migration_id}")
        else:
            print("Database already up to date.")
