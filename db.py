"""
Storage layer for the ticketing app: SQLite, one table for tickets plus a
signature index used for dedup. Deliberately simple (no ORM) so it's easy
to read and to later swap for the real feedback_collector.py backend if
you want tickets and Copilot-feedback traces to live in one place.

This module reads and writes rows only. The schema -- every CREATE, ALTER and
INDEX -- belongs to migrate_db.py, so there is exactly one place that describes
what the database looks like and how it got that way.
"""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from migrate_db import db_path
from version import APP_VERSION

# Admins never close a ticket -- they resolve it and hand it back. Only the
# submitter can settle it, by verifying, and only then does it stop taking input.
RESOLVED_STATUSES = ("in_verification",)
LOCKED_STATUSES = ("verified",)


def get_conn():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")



def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def compute_signature(
    toolkit: str, categories: str, prompt: str, title: str, description: str
) -> str:
    """
    A crude failure-signature: normalize and hash the toolkit + categories + the
    prompt + a stripped version of the title/description (numbers, hex, and
    timestamps removed so near-identical reports collapse together).
    The prompt is the strongest signal here -- two people asking the same thing
    and hitting the same wall is exactly the case worth collapsing -- but the
    toolkit scopes it, since the same question of two toolkits is two problems.
    This mirrors, at a much smaller scale, the Tier-1 signature dedup
    already used by rad_core.feedback / scripts/feedback_collector.py in
    rad-agent-toolkit -- swap this for that implementation if you want a
    single dedup engine across both systems.
    """
    text = f"{toolkit}|{categories}|{prompt}|{title}\n{description}".lower()
    text = re.sub(r"0x[0-9a-f]+", "<hex>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def create_ticket(data: dict) -> int:
    signature = compute_signature(
        data.get("toolkit", ""),
        data.get("categories", ""),
        data.get("prompt", ""),
        data["title"],
        data["description"],
    )
    with closing(get_conn()) as conn:
        # Dedup: if an open ticket shares this signature, link as duplicate
        existing = conn.execute(
            "SELECT id FROM tickets WHERE signature = ? AND status NOT IN ('closed','duplicate') "
            "ORDER BY id ASC LIMIT 1",
            (signature,),
        ).fetchone()
        now = _now()
        cur = conn.execute(
            """INSERT INTO tickets
               (signature, toolkit, categories, knowledge_sources,
                knowledge_scope, operations, title, description, expected_behavior,
                actual_behavior, prompt, transcript, suggestion, toolkit_version, severity,
                ticket_track, self_hosted_area, self_hosted_metric, self_hosted_result,
                self_hosted_score, self_hosted_target, self_hosted_measurement,
                self_hosted_evidence, self_hosted_doc_ref,
                status, duplicate_of, submitter_username, submitter_email, created_at,
                updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                signature,
                data.get("toolkit", ""),
                data.get("categories", ""),
                data.get("knowledge_sources", ""),
                data.get("knowledge_scope", ""),
                data.get("operations", ""),
                data["title"],
                data["description"],
                data.get("expected_behavior", ""),
                data.get("actual_behavior", ""),
                data.get("prompt", ""),
                data.get("transcript", ""),
                data.get("suggestion", ""),
                data.get("toolkit_version", ""),
                data.get("severity", "normal"),
                data.get("ticket_track", "toolkit"),
                data.get("self_hosted_area", ""),
                data.get("self_hosted_metric", ""),
                data.get("self_hosted_result", ""),
                data.get("self_hosted_score", ""),
                data.get("self_hosted_target", ""),
                data.get("self_hosted_measurement", ""),
                data.get("self_hosted_evidence", ""),
                data.get("self_hosted_doc_ref", ""),
                "duplicate" if existing else "entered",
                existing["id"] if existing else None,
                data["submitter_username"],
                data.get("submitter_email", ""),
                now,
                now,
            ),
        )
        conn.commit()
        return cur.lastrowid


def list_tickets(status: str = None, toolkit: str = None) -> list:
    rows, _ = search_tickets(status=status, toolkit=toolkit, page_size=0)
    return rows


PAGE_SIZES = (10, 25, 50, 100)
# Columns a free-text search looks through; the id is matched as text so
# searching "83" finds ticket #83.
SEARCH_COLUMNS = ("CAST(id AS TEXT)", "title", "prompt", "transcript", "suggestion",
                  "description", "submitter_username", "fixed_answer", "resolution_note")

# Sortable columns, keyed by the name used in the URL. Anything not listed here
# is ignored, so the sort parameter can never reach SQL unchecked.
SORT_COLUMNS = {
    "id": "id",
    "toolkit": "toolkit COLLATE NOCASE",
    "title": "title COLLATE NOCASE",
    "categories": "categories COLLATE NOCASE",
    "severity": None,      # ranked below, not alphabetical
    "status": None,        # ranked by workflow order
    "submitter": "submitter_username COLLATE NOCASE",
    "updated": "updated_at",
}
DEFAULT_SORT = "id"
# Sorting these alphabetically would be useless: "high, low, normal".
STATUS_ORDER = ("entered", "working_on_it", "known_limitation", "solved", "in_verification",
                "verified", "duplicate")


def _order_by(sort: str, direction: str, severity_order: tuple) -> tuple:
    """Return (sql_fragment, params) for a validated sort key."""
    if sort not in SORT_COLUMNS:
        sort = DEFAULT_SORT
    direction = "ASC" if str(direction).lower() == "asc" else "DESC"

    params = []
    if sort == "severity":
        ranks = severity_order or ("low", "normal", "high")
        cases = " ".join(f"WHEN ? THEN {i}" for i in range(len(ranks)))
        expr = f"CASE severity {cases} ELSE {len(ranks)} END"
        params.extend(ranks)
    elif sort == "status":
        cases = " ".join(f"WHEN ? THEN {i}" for i in range(len(STATUS_ORDER)))
        expr = f"CASE status {cases} ELSE {len(STATUS_ORDER)} END"
        params.extend(STATUS_ORDER)
    else:
        expr = SORT_COLUMNS[sort]

    tiebreak = "" if sort == "id" else ", id DESC"
    return f" ORDER BY {expr} {direction}{tiebreak}", params


def search_tickets(status: str = None, toolkit: str = None, submitter: str = None,
                   q: str = None, page: int = 1, page_size: int = 0,
                   sort: str = DEFAULT_SORT, direction: str = "desc",
                   severity_order: tuple = ()) -> tuple:
    """Filtered, searched, sorted and paged ticket list. Returns (rows, total)."""
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if toolkit:
        clauses.append("toolkit = ?")
        params.append(toolkit)
    if submitter:
        clauses.append("submitter_username = ?")
        params.append(submitter)
    if q:
        like = f"%{q.strip()}%"
        clauses.append("(" + " OR ".join(f"{c} LIKE ?" for c in SEARCH_COLUMNS) + ")")
        params.extend([like] * len(SEARCH_COLUMNS))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order_sql, order_params = _order_by(sort, direction, severity_order)

    with closing(get_conn()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM tickets{where}", params).fetchone()[0]
        sql = f"SELECT * FROM tickets{where}{order_sql}"
        page_params = list(params) + list(order_params)
        if page_size:
            sql += " LIMIT ? OFFSET ?"
            page_params += [page_size, max(0, (page - 1) * page_size)]
        rows = conn.execute(sql, page_params).fetchall()
        return [dict(r) for r in rows], total


def get_ticket(ticket_id: int) -> dict:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        return dict(row) if row else None


def update_status(ticket_id: int, status: str):
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), ticket_id),
        )
        conn.commit()


# What a submitter may change on their own ticket. Status, resolution and
# ownership are deliberately not in here.
EDITABLE_FIELDS = ("toolkit", "categories", "knowledge_sources", "knowledge_scope",
                   "operations", "prompt", "transcript", "suggestion",
                   "toolkit_version", "severity", "title", "description",
                   "ticket_track", "self_hosted_area", "self_hosted_metric",
                   "self_hosted_result", "self_hosted_score", "self_hosted_target",
                   "self_hosted_measurement", "self_hosted_evidence", "self_hosted_doc_ref")


def update_ticket(ticket_id: int, data: dict):
    signature = compute_signature(
        data.get("toolkit", ""),
        data.get("categories", ""),
        data.get("prompt", ""),
        data["title"],
        data["description"],
    )
    assignments = ", ".join(f"{f} = ?" for f in EDITABLE_FIELDS)
    values = [data.get(f, "") for f in EDITABLE_FIELDS]
    with closing(get_conn()) as conn:
        conn.execute(
            f"UPDATE tickets SET {assignments}, signature = ?, updated_at = ? WHERE id = ?",
            values + [signature, _now(), ticket_id],
        )
        conn.commit()


def delete_ticket(ticket_id: int):
    """Foreign keys are off by default in SQLite, so clear the comments too."""
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM ticket_comments WHERE ticket_id = ?", (ticket_id,))
        conn.execute("UPDATE tickets SET duplicate_of = NULL WHERE duplicate_of = ?", (ticket_id,))
        conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        conn.commit()


def is_locked(ticket: dict) -> bool:
    return ticket["status"] in LOCKED_STATUSES


def awaiting_verification(ticket: dict) -> bool:
    return ticket["status"] in RESOLVED_STATUSES


def resolve_ticket(ticket_id: int, resolution: str, status: str, resolved_by: str,
                   fixed_in_versions: str = "", fixed_answer: str = "", note: str = ""):
    """Admin hand-back: record the outcome and wait for the submitter to verify."""
    now = _now()
    with closing(get_conn()) as conn:
        conn.execute(
            """UPDATE tickets
               SET status = ?, resolution = ?, resolution_note = ?,
                   fixed_in_versions = ?, fixed_answer = ?, resolved_by = ?, resolved_at = ?,
                   verified_by = NULL, verified_at = NULL, updated_at = ?
               WHERE id = ?""",
            (status, resolution, note, fixed_in_versions, fixed_answer, resolved_by, now,
             now, ticket_id),
        )
        conn.commit()


def verify_ticket(ticket_id: int, verified_by: str):
    """The submitter accepting the outcome -- the only way a ticket reaches its end state."""
    now = _now()
    with closing(get_conn()) as conn:
        conn.execute(
            """UPDATE tickets
               SET status = 'verified', verified_by = ?, verified_at = ?, updated_at = ?
               WHERE id = ?""",
            (verified_by, now, now, ticket_id),
        )
        conn.commit()


def reopen_ticket(ticket_id: int, status: str = "entered"):
    """Reopening clears the outcome so a stale resolution can't linger on an open ticket."""
    with closing(get_conn()) as conn:
        conn.execute(
            """UPDATE tickets
               SET status = ?, resolution = NULL, resolution_note = NULL,
                   fixed_in_versions = NULL, fixed_answer = NULL, resolved_by = NULL,
                   resolved_at = NULL, verified_by = NULL, verified_at = NULL,
                   updated_at = ?
               WHERE id = ?""",
            (status, _now(), ticket_id),
        )
        conn.commit()


def add_comment(ticket_id: int, author_username: str, author_display: str, body: str,
                is_admin_note: bool = False) -> int:
    with closing(get_conn()) as conn:
        cur = conn.execute(
            """INSERT INTO ticket_comments
               (ticket_id, author_username, author_display, body, is_admin_note, created_at)
               VALUES (?,?,?,?,?,?)""",
            (ticket_id, author_username, author_display, body, 1 if is_admin_note else 0, _now()),
        )
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (_now(), ticket_id))
        conn.commit()
        return cur.lastrowid


def list_comments(ticket_id: int) -> list:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM ticket_comments WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def comment_counts() -> dict:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT ticket_id, COUNT(*) AS n FROM ticket_comments GROUP BY ticket_id"
        ).fetchall()
        return {r["ticket_id"]: r["n"] for r in rows}


def export_as_eval_case(ticket_id: int, export_dir: str = "exports") -> str:
    """
    Write the ticket out as a JSON stub shaped like an eval case, ready to
    drop into rad-agent-toolkit's tests/evals/cases/. This is the "teach
    the repo" step: a triaged, confirmed-real ticket becomes a regression
    case instead of just staying a support ticket.
    """
    import os

    ticket = get_ticket(ticket_id)
    if not ticket:
        raise ValueError(f"No such ticket: {ticket_id}")

    os.makedirs(export_dir, exist_ok=True)
    case = {
        "case_id": f"ticket-{ticket_id}-{ticket['signature']}",
        "source": "ticketing_app",
        "source_version": APP_VERSION,
        "toolkit": ticket["toolkit"],
        "categories": [c for c in (ticket["categories"] or "").split(",") if c],
        "knowledge_sources": [s for s in (ticket["knowledge_sources"] or "").split(",") if s],
        "knowledge_scope": [s for s in (ticket["knowledge_scope"] or "").split(",") if s],
        "operations": [o for o in (ticket["operations"] or "").split(",") if o],
        "title": ticket["title"],
        "description": ticket["description"],
        "prompt": ticket["prompt"],
        "suggestion": ticket["suggestion"],
        "expected_behavior": ticket["expected_behavior"],
        "actual_behavior": ticket["actual_behavior"],
        "transcript": ticket["transcript"],
        "toolkit_version_reported": ticket["toolkit_version"],
        "resolution": ticket["resolution"],
        "resolution_note": ticket["resolution_note"],
        "fixed_in_versions": ticket["fixed_in_versions"],
        "expected_answer": ticket["fixed_answer"],
        "ticket_track": ticket.get("ticket_track") or "toolkit",
        "self_hosted_area": ticket.get("self_hosted_area"),
        "self_hosted_metric": ticket.get("self_hosted_metric"),
        "self_hosted_result": ticket.get("self_hosted_result"),
        "self_hosted_score": ticket.get("self_hosted_score"),
        "self_hosted_target": ticket.get("self_hosted_target"),
        "self_hosted_measurement": ticket.get("self_hosted_measurement"),
        "self_hosted_evidence": ticket.get("self_hosted_evidence"),
        "self_hosted_doc_ref": ticket.get("self_hosted_doc_ref"),
        "submitted_by": ticket["submitter_username"],
        "submitted_at": ticket["created_at"],
        "verified_by": ticket["verified_by"],
        "follow_ups": [
            {"author": c["author_username"], "at": c["created_at"], "body": c["body"]}
            for c in list_comments(ticket_id)
        ],
    }
    path = os.path.join(export_dir, f"{case['case_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(case, f, indent=2)

    # Promotion is orthogonal to the workflow: it must not overwrite the status.
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE tickets SET promoted_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), ticket_id),
        )
        conn.commit()
    return path
