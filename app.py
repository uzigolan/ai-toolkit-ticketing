"""
RAD AI Ticketing Center.

A small Flask app where any employee can submit a structured ticket about any
of the RAD AI toolkits (rad-agent-toolkit, radview-ai-toolkit,
pikachu-ai-toolkit, synergy-ccm-mcp, and whatever comes next -- the list lives
in categories.yml). You triage in the /admin queue, and "Promote to eval case"
exports a ticket as a JSON stub shaped for the toolkit's tests/evals/cases/ --
that's the loop that actually teaches the repo, per docs/feedback-loop.md.

Authentication supports local accounts and company LDAP at the same time,
following the PKI repo's model: every user row carries an ``auth_source``
and login verifies against whichever backend that row names. With no
directory configured the app runs fine on local accounts alone.

Run:
    pip install -r requirements.txt
    python app.py
Then browse to http://localhost:5000
"""
import configparser
import logging
import os
import secrets
import threading

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from markupsafe import Markup, escape
from werkzeug.datastructures import MultiDict
from werkzeug.serving import make_server

import db
import migrate_db
import taxonomy
import users
from ldap_auth import ldap_authenticate
from version import APP_NAME, APP_VERSION

app = Flask(__name__)
app.secret_key = os.environ.get("TICKETING_SECRET_KEY", "change-me-in-config")
logging.basicConfig(level=logging.INFO)

# Categories, sub-categories, families and severities are vocabulary, not code:
# they come from categories.yml so they can be changed without touching Python.
TAXONOMY = taxonomy.load(logger=app.logger)
TOOLKITS = TAXONOMY["toolkits"]
TOOLKIT_LABELS = dict(TOOLKITS)
DEFAULT_TOOLKIT = TAXONOMY.get("default_toolkit") or (TOOLKITS[0][0] if TOOLKITS else "")
CATEGORIES = TAXONOMY["categories"]
CATEGORY_LABELS = dict(CATEGORIES)
KNOWLEDGE_SOURCES = TAXONOMY["knowledge_sources"]
KNOWLEDGE_SCOPE = TAXONOMY["knowledge_scope"]
SCOPE_LABELS = {s["id"]: s["label"] for s in KNOWLEDGE_SCOPE}
DEFAULT_SCOPE = [s["id"] for s in KNOWLEDGE_SCOPE if s["default"]]
OPERATIONS = TAXONOMY["operations"]
SOURCE_LABELS = dict(KNOWLEDGE_SOURCES)
OPERATION_LABELS = dict(OPERATIONS)
SEVERITIES = TAXONOMY["severities"]
SEVERITY_IDS = [s[0] for s in SEVERITIES]
RESOLUTIONS = TAXONOMY["resolutions"]
RESOLUTION_LABELS = {r["id"]: r["label"] for r in RESOLUTIONS}
RESOLUTION_STATUS = {r["id"]: r["status"] for r in RESOLUTIONS}
RESOLUTIONS_NEEDING_VERSION = {r["id"] for r in RESOLUTIONS if r["requires_version"]}
RESOLUTIONS_NEEDING_ANSWER = {r["id"] for r in RESOLUTIONS if r["requires_answer"]}
RESOLUTIONS_NEEDING_NOTE = {r["id"] for r in RESOLUTIONS if r["requires_note"]}
DEFAULT_SEVERITY = "normal" if "normal" in SEVERITY_IDS else SEVERITY_IDS[0]

# Usernames listed here get the admin role the first time they are seen
# (local creation or LDAP auto-provision). Ongoing role changes live in the
# users table; swap this for an LDAP group-membership check if you have one.
ADMIN_USERNAMES = set(
    u.strip().lower() for u in os.environ.get("TICKETING_ADMINS", "").split(",") if u.strip()
)


def load_ldap_config():
    """Read [LDAP] from config.ini. configparser lowercases keys, so restore them."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    if "LDAP" not in cfg:
        return {}
    return {k.upper(): v for k, v in cfg["LDAP"].items()}


LDAP_CONFIG = load_ldap_config()
# LDAP is on only when explicitly enabled, or implicitly when a host is set.
LDAP_ENABLED = str(
    LDAP_CONFIG.get("ENABLED", "true" if LDAP_CONFIG.get("LDAP_HOST") else "false")
).strip().lower() in ("1", "true", "yes", "on")
if not LDAP_CONFIG.get("LDAP_HOST"):
    LDAP_ENABLED = False
app.config["LDAP_ENABLED"] = LDAP_ENABLED


def load_server_config():
    """Ports and TLS material from config.ini; all optional."""
    cfg = configparser.ConfigParser()
    cfg.read("config.ini")
    return {
        "http_enabled": cfg.getboolean("SERVER", "http_enabled", fallback=True),
        "http_port": cfg.getint("SERVER", "http_port", fallback=5000),
        "https_enabled": cfg.getboolean("HTTPS", "enabled", fallback=False),
        "https_port": cfg.getint("HTTPS", "port", fallback=5443),
        "ssl_cert": cfg.get("HTTPS", "ssl_cert", fallback="").strip(),
        "ssl_key": cfg.get("HTTPS", "ssl_key", fallback="").strip(),
    }


SERVER_CONFIG = load_server_config()


def _csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def _local_time(value: str, mode: str = "datetime") -> Markup:
    """
    Timestamps are stored UTC. Emit the machine-readable value and a two-digit
    fallback; base.html rewrites it to the reader's own timezone.
    """
    if not value:
        return Markup("&mdash;")
    text = str(value)
    short_date = text[2:10]           # 2026-08-22 -> 26-08-22
    short_time = text[11:16]          # 15:07:39   -> 15:07
    fallback = {"date": short_date, "time": short_time}.get(
        mode, f"{short_date} {short_time}".strip()
    )
    iso = text.replace(" ", "T") + "Z"
    return Markup(
        f'<time class="ts" data-utc="{escape(iso)}" data-mode="{escape(mode)}">'
        f"{escape(fallback)}</time>"
    )


@app.context_processor
def inject_auth_context():
    def list_args(**overrides):
        """Current query string with a few keys replaced, so filters compose."""
        args = request.args.to_dict()
        args.update(overrides)
        # A changed filter always sends you back to the first page.
        if "page" not in overrides:
            args.pop("page", None)
        return {k: v for k, v in args.items() if v not in (None, "")}

    return {
        "ldap_enabled": LDAP_ENABLED,
        "csrf_token": _csrf_token,
        "local_time": _local_time,
        "list_args": list_args,
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "toolkits": TOOLKITS,
        "default_toolkit": DEFAULT_TOOLKIT,
        "toolkit_label": lambda t: TOOLKIT_LABELS.get(t, t or ""),
        "category_label": lambda c: CATEGORY_LABELS.get(c, c),
        "resolution_label": lambda r: RESOLUTION_LABELS.get(r, r or ""),
        "source_label": lambda s: SOURCE_LABELS.get(s, s),
        "scope_label": lambda s: SCOPE_LABELS.get(s, s),
        "operation_label": lambda o: OPERATION_LABELS.get(o, o),
        "as_ids": lambda csv: [v for v in (csv or "").split(",") if v],
    }


@app.before_request
def _csrf_protect():
    if request.method == "POST":
        token = session.get("_csrf_token")
        if not token or not secrets.compare_digest(token, request.form.get("_csrf_token", "")):
            abort(400)


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _start_session(user: dict):
    users.touch_login(user["id"])
    session.clear()
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["display_name"] = user["display_name"] or user["username"]
    session["email"] = user["email"] or ""
    session["role"] = user["role"]
    session["auth_source"] = user["auth_source"]


def _safe_next(target: str) -> str:
    """Only allow same-site, path-only redirects after login."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return None
    return target


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = _safe_next(request.args.get("next", ""))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("login.html")

        # One generic message on every failure path, so the form never reveals
        # whether a username exists or which backend it authenticates against.
        invalid = "Invalid username or password."
        user = users.get_user(username)

        if user and user["status"] != "active":
            app.logger.warning("Login attempt for disabled account: %s", username)
            flash("This account is disabled. Contact an administrator.", "error")
            return render_template("login.html")

        if user and user["auth_source"] == "local":
            if users.verify_password(user, password):
                _start_session(user)
                app.logger.info("User %s logged in (local).", user["username"])
                return redirect(next_url or url_for("new_ticket"))
            app.logger.warning("Failed local login for: %s", username)
            flash(invalid, "error")
            return render_template("login.html")

        if not LDAP_ENABLED:
            app.logger.warning("Failed login for %s (no local account; LDAP off).", username)
            flash(invalid, "error")
            return render_template("login.html")

        result = ldap_authenticate(username, password, LDAP_CONFIG, app.logger)
        if not result:
            app.logger.warning("Failed LDAP login for: %s", username)
            flash(invalid, "error")
            return render_template("login.html")

        if user:
            users.update_profile(
                user["id"], email=result.get("email"), display_name=result.get("display_name")
            )
            user = users.get_user_by_id(user["id"])
        else:
            # First successful bind provisions the local row for this LDAP user.
            role = "admin" if username.lower() in ADMIN_USERNAMES else "user"
            user_id = users.create_user(
                username,
                role=role,
                email=result.get("email"),
                display_name=result.get("display_name"),
                auth_source="ldap",
            )
            user = users.get_user_by_id(user_id)
            app.logger.info("Provisioned LDAP user %s as %s.", username, role)

        _start_session(user)
        app.logger.info("User %s logged in (LDAP).", user["username"])
        return redirect(next_url or url_for("new_ticket"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if session.get("auth_source") != "local":
        flash("Your password is managed by the company directory.", "info")
        return redirect(url_for("new_ticket"))
    if request.method == "POST":
        user = users.get_user_by_id(session["user_id"])
        new = request.form.get("new_password", "")
        if not users.verify_password(user, request.form.get("current_password", "")):
            flash("Current password is incorrect.", "error")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new != request.form.get("confirm_password", ""):
            flash("New passwords do not match.", "error")
        else:
            users.set_password(user["id"], new)
            flash("Password updated.", "success")
            return redirect(url_for("new_ticket"))
    return render_template("change_password.html")


@app.route("/", methods=["GET"])
@login_required
def index():
    return redirect(url_for("new_ticket"))


CHAT_SPEAKERS = ("user", "assistant", "github copilot", "copilot", "you", "me")


def _derive_title(prompt: str, transcript: str, description: str) -> str:
    """
    When someone just pastes a chat and submits, invent a usable one-liner from
    the prompt (or failing that the first substantial line) so the triage queue
    stays readable.
    """
    for source in (prompt, description, transcript):
        for raw in (source or "").splitlines():
            line = raw.strip().lstrip("#>*-•").strip()
            # Drop the speaker prefix chat UIs paste in ("GitHub Copilot: ...").
            speaker, sep, rest = line.partition(":")
            if sep and speaker.strip().lower() in CHAT_SPEAKERS:
                line = rest.strip()
            if len(line) < 8:
                continue
            return line[:120]
    return "Untitled report"


def _checked_ids(field: str, allowed: dict) -> str:
    """Multi-selects come back as a list; keep the known ids, in taxonomy order."""
    chosen = set(request.form.getlist(field))
    return ",".join(key for key in allowed if key in chosen)


def _ticket_form_data() -> dict:
    """Validated ticket fields from the submit/edit form."""
    severity = request.form.get("severity", "")
    if severity not in SEVERITY_IDS:
        severity = DEFAULT_SEVERITY
    transcript = request.form.get("transcript", "").strip()
    prompt = request.form.get("prompt", "").strip()
    return {
        "toolkit": request.form.get("toolkit", ""),
        "categories": _checked_ids("categories", CATEGORY_LABELS),
        "knowledge_sources": _checked_ids("knowledge_sources", SOURCE_LABELS),
        "knowledge_scope": _checked_ids("knowledge_scope", SCOPE_LABELS),
        "operations": _checked_ids("operations", OPERATION_LABELS),
        "title": _derive_title(prompt, transcript, ""),
        "description": transcript or prompt,
        "prompt": prompt,
        "transcript": transcript,
        "suggestion": request.form.get("suggestion", "").strip(),
        "toolkit_version": request.form.get("toolkit_version", "").strip(),
        "severity": severity,
    }


def _ticket_form_error(data: dict) -> str:
    if data["toolkit"] not in TOOLKIT_LABELS:
        return "Choose which toolkit this ticket is about."
    if not data["categories"]:
        return "Pick at least one category — what went wrong?"
    if not data["description"]:
        return "Paste the chat, or at least the prompt, before submitting."
    return ""


def _render_ticket_form(form, action_url, submit_label, heading, subtitle, ticket=None):
    return render_template(
        "new_ticket.html",
        categories=CATEGORIES,
        severities=SEVERITIES,
        knowledge_sources=KNOWLEDGE_SOURCES,
        knowledge_scope=KNOWLEDGE_SCOPE,
        default_scope=DEFAULT_SCOPE,
        operations=OPERATIONS,
        default_severity=DEFAULT_SEVERITY,
        form=form,
        action_url=action_url,
        submit_label=submit_label,
        heading=heading,
        form_subtitle=subtitle,
        ticket=ticket,
    )


NEW_TICKET_SUBTITLE = ("Pick the toolkit, tick what went wrong, paste the whole chat, "
                       "submit. Everything else helps triage but is optional.")


@app.route("/tickets/new", methods=["GET", "POST"])
@login_required
def new_ticket():
    if request.method == "POST":
        form_data = _ticket_form_data()
        form_data["submitter_username"] = session["username"]
        form_data["submitter_email"] = session.get("email", "")

        error = _ticket_form_error(form_data)
        if error:
            flash(error, "error")
            return _render_ticket_form(
                request.form, url_for("new_ticket"), "Submit ticket",
                "Submit a ticket", NEW_TICKET_SUBTITLE,
            )

        ticket_id = db.create_ticket(form_data)
        ticket = db.get_ticket(ticket_id)
        if ticket["status"] == "duplicate":
            flash(
                f"Thanks — this looks like a duplicate of ticket #{ticket['duplicate_of']}, "
                f"linked automatically.",
                "info",
            )
        else:
            flash(f"Ticket #{ticket_id} submitted. Thanks for the report.", "success")
        return redirect(url_for("my_tickets"))

    return _render_ticket_form(
        MultiDict(), url_for("new_ticket"), "Submit ticket",
        "Submit a ticket", NEW_TICKET_SUBTITLE,
    )


@app.route("/tickets/<int:ticket_id>/edit", methods=["GET", "POST"])
@login_required
def edit_ticket(ticket_id):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        abort(404)
    # Only the person who filed it, and only while it is still open.
    if ticket["submitter_username"] != session["username"] or db.is_locked(ticket):
        abort(403)

    if request.method == "POST":
        form_data = _ticket_form_data()
        error = _ticket_form_error(form_data)
        if error:
            flash(error, "error")
        else:
            db.update_ticket(ticket_id, form_data)
            flash(f"Ticket #{ticket_id} updated.", "success")
            return redirect(url_for("ticket_detail", ticket_id=ticket_id))
        form = request.form
    else:
        form = MultiDict()
        for field in ("toolkit", "prompt", "transcript", "suggestion",
                      "toolkit_version", "severity"):
            form[field] = ticket[field] or ""
        for field in ("categories", "knowledge_sources", "knowledge_scope", "operations"):
            for value in (ticket[field] or "").split(","):
                if value:
                    form.add(field, value)

    return _render_ticket_form(
        form, url_for("edit_ticket", ticket_id=ticket_id), "Save changes",
        f"Edit ticket #{ticket_id}",
        "Changing the prompt or the chat re-derives the title and the dedup signature.",
        ticket=ticket,
    )


@app.route("/tickets/<int:ticket_id>/delete", methods=["POST"])
@login_required
def delete_ticket(ticket_id):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        abort(404)
    is_admin = session.get("role") == "admin"
    is_owner = ticket["submitter_username"] == session["username"]
    # Owners can withdraw their own open ticket; admins can remove anything.
    if not (is_admin or (is_owner and not db.is_locked(ticket))):
        abort(403)
    db.delete_ticket(ticket_id)
    flash(f"Ticket #{ticket_id} deleted.", "success")
    return redirect(url_for("my_tickets" if is_owner else "admin_queue"))


def _list_params():
    """Search, paging and filter values shared by the three ticket lists."""
    try:
        page_size = int(request.args.get("page_size", 25))
    except ValueError:
        page_size = 25
    if page_size not in db.PAGE_SIZES:
        page_size = 25
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    return {
        "status": request.args.get("status") or None,
        "toolkit": request.args.get("toolkit") or None,
        "q": (request.args.get("q") or "").strip() or None,
        "page": page,
        "page_size": page_size,
        "sort": request.args.get("sort") or db.DEFAULT_SORT,
        "direction": "asc" if request.args.get("dir") == "asc" else "desc",
    }


def _render_list(title, subtitle, list_route, submitter=None):
    params = _list_params()
    tickets, total = db.search_tickets(
        submitter=submitter, severity_order=tuple(SEVERITY_IDS), **params
    )
    total_pages = max(1, -(-total // params["page_size"]))
    return render_template(
        "tickets.html",
        tickets=tickets,
        counts=db.comment_counts(),
        title=title,
        subtitle=subtitle,
        list_route=list_route,
        active_status=params["status"],
        active_toolkit=params["toolkit"],
        search_query=params["q"] or "",
        sort=params["sort"] if params["sort"] in db.SORT_COLUMNS else db.DEFAULT_SORT,
        direction=params["direction"],
        page=min(params["page"], total_pages),
        page_size=params["page_size"],
        page_sizes=db.PAGE_SIZES,
        total=total,
        total_pages=total_pages,
    )


@app.route("/tickets")
@login_required
def all_tickets():
    """Everyone can read every ticket -- seeing what's already reported is what
    stops the same issue being filed five times."""
    return _render_list(
        "All tickets",
        "Everything reported about the AI toolkits, by anyone.",
        "all_tickets",
    )


@app.route("/tickets/mine")
@login_required
def my_tickets():
    return _render_list(
        "My tickets",
        "Tickets you submitted. You can add follow-ups, and you decide when "
        "one is verified or goes back to open.",
        "my_tickets",
        submitter=session["username"],
    )


@app.route("/tickets/<int:ticket_id>", methods=["GET", "POST"])
@login_required
def ticket_detail(ticket_id):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        abort(404)

    is_admin = session.get("role") == "admin"
    is_owner = ticket["submitter_username"] == session["username"]
    locked = db.is_locked(ticket)
    awaiting = db.awaiting_verification(ticket)
    # Owners keep adding detail to their own report until they verify it closed;
    # admins can always add, including to reopen the conversation.
    can_comment = is_admin or (is_owner and not locked)
    # Only the person who filed it can settle it -- an admin closing their own
    # bug report is exactly the rubber-stamping this flow is meant to prevent.
    can_verify = is_owner and awaiting
    can_reopen = (is_owner or is_admin) and (awaiting or locked)

    if request.method == "POST":
        action = request.form.get("action", "comment")

        if action == "comment":
            if not can_comment:
                abort(403)
            body = request.form.get("body", "").strip()
            if not body:
                flash("Write something before adding it to the ticket.", "error")
            else:
                db.add_comment(
                    ticket_id,
                    session["username"],
                    session.get("display_name") or session["username"],
                    body,
                    is_admin_note=is_admin and not is_owner,
                )
                flash("Added to the ticket.", "success")

        elif action == "verify":
            if not can_verify:
                abort(403)
            db.verify_ticket(ticket_id, session["username"])
            flash("Verified and closed. Thanks for confirming.", "success")

        elif action == "reopen":
            if not can_reopen:
                abort(403)
            db.reopen_ticket(ticket_id)
            flash("Reopened; the previous outcome was cleared.", "success")

        elif is_admin:
            if action == "triage":
                db.update_status(ticket_id, "triaged")
                flash("Marked triaged.", "success")
            elif action == "resolve":
                resolution = request.form.get("resolution", "")
                fixed_in = request.form.get("fixed_in_versions", "").strip()
                fixed_answer = request.form.get("fixed_answer", "").strip()
                note = request.form.get("resolution_note", "").strip()
                label = RESOLUTION_LABELS.get(resolution, resolution)
                if resolution not in RESOLUTION_LABELS:
                    flash("Choose an outcome before handing the ticket back.", "error")
                elif resolution in RESOLUTIONS_NEEDING_VERSION and not fixed_in:
                    flash(
                        f"'{label}' needs the `rad agent show versions` output of the "
                        f"build that carries the fix.",
                        "error",
                    )
                elif resolution in RESOLUTIONS_NEEDING_ANSWER and not fixed_answer:
                    flash(
                        f"'{label}' needs the corrected answer — re-run the submitter's "
                        f"prompt on the fixed build and paste what it says now.",
                        "error",
                    )
                elif resolution in RESOLUTIONS_NEEDING_NOTE and not note:
                    flash(f"'{label}' needs a note explaining the decision.", "error")
                else:
                    db.resolve_ticket(
                        ticket_id,
                        resolution,
                        RESOLUTION_STATUS[resolution],
                        resolved_by=session["username"],
                        fixed_in_versions=fixed_in,
                        fixed_answer=fixed_answer,
                        note=note,
                    )
                    flash(
                        f"Marked '{label}'. {ticket['submitter_username']} decides "
                        f"whether it's verified or goes back to open.",
                        "success",
                    )
            elif action == "promote":
                path = db.export_as_eval_case(ticket_id)
                flash(
                    f"Exported to {path} — copy into the toolkit's tests/evals/cases/.",
                    "success",
                )
            else:
                abort(400)
        else:
            abort(403)
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    return render_template(
        "ticket_detail.html",
        ticket=ticket,
        comments=db.list_comments(ticket_id),
        is_admin=is_admin,
        is_owner=is_owner,
        locked=locked,
        awaiting=awaiting,
        can_comment=can_comment,
        can_verify=can_verify,
        can_reopen=can_reopen,
        resolutions=RESOLUTIONS,
    )


@app.route("/admin")
@admin_required
def admin_queue():
    return _render_list(
        "Triage queue",
        "Every ticket, with the admin actions on each one.",
        "admin_queue",
    )



@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "user")
        try:
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters.")
            users.create_user(
                username,
                password=password,
                role=role,
                email=request.form.get("email", "").strip(),
                display_name=request.form.get("display_name", "").strip() or None,
                auth_source="local",
            )
            flash(f"Local account '{username}' created.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin_users"))
    return render_template("users.html", user_list=users.list_users())


@app.route("/admin/users/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_user_action(user_id, action):
    target = users.get_user_by_id(user_id)
    if not target:
        abort(404)
    self_target = target["id"] == session.get("user_id")
    # Guard rails: never let an admin lock everyone (including themselves) out.
    demoting = action in ("role", "disable", "delete")
    last_admin = target["role"] == "admin" and users.count_admins() <= 1

    try:
        if demoting and self_target:
            raise ValueError("You cannot change your own role or account status.")
        if demoting and last_admin:
            raise ValueError("This is the last active admin; promote someone else first.")

        if action == "role":
            new_role = request.form.get("role", "user")
            users.set_role(user_id, new_role)
            flash(f"{target['username']} is now {new_role}.", "success")
        elif action == "disable":
            users.set_status(user_id, "disabled")
            flash(f"{target['username']} disabled.", "success")
        elif action == "enable":
            users.set_status(user_id, "active")
            flash(f"{target['username']} enabled.", "success")
        elif action == "password":
            new = request.form.get("new_password", "")
            if len(new) < 8:
                raise ValueError("Password must be at least 8 characters.")
            users.set_password(user_id, new)
            flash(f"Password reset for {target['username']}.", "success")
        elif action == "delete":
            users.delete_user(user_id)
            flash(f"{target['username']} deleted.", "success")
        else:
            abort(404)
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_users"))


def bootstrap():
    migrate_db.migrate(logger=app.logger)
    generated = users.ensure_bootstrap_admin(
        os.environ.get("TICKETING_BOOTSTRAP_ADMIN", "admin"),
        os.environ.get("TICKETING_BOOTSTRAP_PASSWORD"),
    )
    if generated:
        app.logger.warning(
            "Created first local admin '%s' with generated password: %s  "
            "(shown once -- change it at /account/password)",
            os.environ.get("TICKETING_BOOTSTRAP_ADMIN", "admin"),
            generated,
        )


bootstrap()

if __name__ == "__main__":
    # The reloader only watches .py by default, so editing the taxonomy would
    # otherwise leave the running app showing the previous vocabulary.
    taxonomy_file = os.environ.get("TICKETING_CATEGORIES_FILE", taxonomy.DEFAULT_FILE)
    extra_files = [taxonomy_file] if os.path.exists(taxonomy_file) else []

    ssl_context = None
    if SERVER_CONFIG["https_enabled"]:
        cert, key = SERVER_CONFIG["ssl_cert"], SERVER_CONFIG["ssl_key"]
        missing = [p or "(unset)" for p in (cert, key) if not p or not os.path.exists(p)]
        if missing:
            # Falling back to plaintext here would silently expose logins.
            raise SystemExit(
                f"config.ini enables HTTPS but these files are missing: {', '.join(missing)}"
            )
        ssl_context = (cert, key)

    http_port = SERVER_CONFIG["http_port"]
    https_port = SERVER_CONFIG["https_port"]
    serve_http = SERVER_CONFIG["http_enabled"] or not ssl_context

    if ssl_context and serve_http:
        # Both listeners share one app. The plain one runs in a thread, and only
        # inside the reloaded child, so a restart doesn't leave two processes
        # fighting over the port. It uses make_server rather than app.run
        # because app.run() inside the reloader child expects to inherit the
        # reloader's socket via WERKZEUG_SERVER_FD, which only the HTTPS
        # listener has.
        if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            http_server = make_server("127.0.0.1", http_port, app, threaded=True)
            threading.Thread(target=http_server.serve_forever, daemon=True).start()
        app.logger.info("Serving HTTP on %s and HTTPS on %s", http_port, https_port)
        app.run(debug=True, port=https_port, ssl_context=ssl_context,
                extra_files=extra_files)
    else:
        port = int(os.environ.get("FLASK_RUN_PORT") or
                   (https_port if ssl_context else http_port))
        app.logger.info("Serving %s on port %s", "HTTPS" if ssl_context else "HTTP", port)
        app.run(debug=True, port=port, ssl_context=ssl_context, extra_files=extra_files)
