"""
User store for the ticketing app: local accounts and LDAP accounts side by
side, following the same shape as the PKI repo's ``user_models.py``.

The key idea, borrowed from there, is a single ``users`` table where every
row carries an ``auth_source`` of either ``local`` or ``ldap``:

  * ``local`` rows hold a werkzeug password hash and are verified in-process.
  * ``ldap`` rows hold an empty hash and are verified by a live bind against
    the directory; the row exists only to carry role/status/profile data.

That lets the app run with no directory at all (local accounts only), with
LDAP only, or with both at once -- the login flow picks per user.

Like db.py, this module only reads and writes rows; the users table itself is
defined in migrate_db.py.
"""
import secrets
import sqlite3
from contextlib import closing

from werkzeug.security import check_password_hash, generate_password_hash

from db import _now, get_conn

ROLES = ("user", "admin")
STATUSES = ("active", "disabled")


def get_user(username: str) -> dict:
    if not username:
        return None
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict:
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def list_users() -> list:
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY auth_source, username COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]


def count_users() -> int:
    with closing(get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def count_admins(active_only: bool = True) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE role = 'admin'"
    if active_only:
        sql += " AND status = 'active'"
    with closing(get_conn()) as conn:
        return conn.execute(sql).fetchone()[0]


def create_user(
    username: str,
    password: str = None,
    role: str = "user",
    email: str = None,
    display_name: str = None,
    status: str = "active",
    auth_source: str = "local",
) -> int:
    """Insert a user. LDAP rows never store a password hash."""
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    if status not in STATUSES:
        raise ValueError(f"Invalid status: {status}")

    if auth_source == "ldap":
        password_hash = ""
    else:
        if not password:
            raise ValueError("A password is required for local accounts.")
        password_hash = generate_password_hash(password)

    with closing(get_conn()) as conn:
        try:
            cur = conn.execute(
                """INSERT INTO users
                   (username, password_hash, role, email, display_name, status,
                    auth_source, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    username,
                    password_hash,
                    role,
                    email or "",
                    display_name or username,
                    status,
                    auth_source,
                    _now(),
                ),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"User '{username}' already exists.")


def verify_password(user: dict, password: str) -> bool:
    """Local-account password check. Always False for LDAP rows."""
    if not user or user.get("auth_source") != "local":
        return False
    stored = user.get("password_hash") or ""
    if not stored:
        return False
    return check_password_hash(stored, password)


def set_password(user_id: int, password: str):
    if not password:
        raise ValueError("Password cannot be empty.")
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError("No such user.")
    if user["auth_source"] != "local":
        raise ValueError("LDAP accounts have no local password to set.")
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user_id),
        )
        conn.commit()


def set_role(user_id: int, role: str):
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")
    with closing(get_conn()) as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        conn.commit()


def set_status(user_id: int, status: str):
    if status not in STATUSES:
        raise ValueError(f"Invalid status: {status}")
    with closing(get_conn()) as conn:
        conn.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
        conn.commit()


def update_profile(user_id: int, email: str = None, display_name: str = None):
    with closing(get_conn()) as conn:
        conn.execute(
            "UPDATE users SET email = COALESCE(?, email), "
            "display_name = COALESCE(?, display_name) WHERE id = ?",
            (email, display_name, user_id),
        )
        conn.commit()


def delete_user(user_id: int):
    with closing(get_conn()) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def touch_login(user_id: int):
    with closing(get_conn()) as conn:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), user_id))
        conn.commit()


def ensure_bootstrap_admin(username: str, password: str = None) -> str:
    """
    Create a first local admin if the user table is empty, so a fresh install
    is reachable without a directory. Returns the generated password when one
    had to be invented (caller should surface it once), else None.
    """
    if count_users() > 0:
        return None
    generated = None
    if not password:
        generated = secrets.token_urlsafe(12)
        password = generated
    create_user(username, password=password, role="admin", auth_source="local")
    return generated
