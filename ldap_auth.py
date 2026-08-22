"""
LDAP authentication for the rad-agent-toolkit ticketing app.

This is one of two auth backends; local accounts live in ``users.py``. A user
whose row has ``auth_source = 'ldap'`` is verified here by a live bind against
the company directory — no password is ever stored for them locally. Nothing
is cached except the identity (username, display name, email) that the app
copies onto their user row for ticket attribution.

Two strategies, same as the PKI repo's ``enterprise/ldap_utils.py``:
  1. Direct bind with guessed DN patterns (cn=/uid= under LDAP_PEOPLE_DN).
  2. Admin bind + search, for directories where the DN can't be guessed
     (typical for Active Directory with sAMAccountName logins).
"""
from typing import Optional, Dict

try:
    from ldap3 import Connection, Server, SUBTREE, ALL
    from ldap3.core.exceptions import LDAPException, LDAPBindError
except ImportError:  # pragma: no cover
    Connection = None
    Server = None
    SUBTREE = None
    ALL = None

    class LDAPException(Exception):
        pass

    class LDAPBindError(LDAPException):
        pass


def _log(logger, level: str, message: str) -> None:
    if logger:
        fn = getattr(logger, level, None)
        if callable(fn):
            fn(message)


def _build_dn_candidates(username: str, base_dn: Optional[str], people_dn: Optional[str]) -> list:
    short = username.split("@", 1)[0] if "@" in username else username
    candidates = []
    for container in (people_dn, base_dn):
        if not container:
            continue
        for rdn in ("cn", "uid", "sAMAccountName"):
            candidates.append(f"{rdn}={username},{container}")
            if short != username:
                candidates.append(f"{rdn}={short},{container}")
    seen, uniq = set(), []
    for dn in candidates:
        if dn not in seen:
            uniq.append(dn)
            seen.add(dn)
    return uniq


def ldap_authenticate(username: str, password: str, cfg: Dict, logger=None) -> Optional[Dict]:
    """Return {'dn', 'email', 'display_name'} on success, else None."""
    if not username or not password:
        return None
    if not Connection or not Server:
        _log(logger, "error", "ldap3 is not installed; cannot authenticate.")
        return None

    host = cfg.get("LDAP_HOST")
    port = int(cfg.get("LDAP_PORT", 389))
    base_dn = cfg.get("LDAP_BASE_DN")
    people_dn = cfg.get("LDAP_PEOPLE_DN")
    admin_dn = cfg.get("LDAP_ADMIN_DN")
    admin_password = cfg.get("LDAP_ADMIN_PASSWORD")
    use_ssl = str(cfg.get("LDAP_USE_SSL", "false")).lower() == "true"
    if not host:
        _log(logger, "error", "LDAP_HOST not configured.")
        return None

    short_username = username.split("@", 1)[0] if "@" in username else username
    server = Server(host, port=port, use_ssl=use_ssl, get_info=ALL, connect_timeout=5)

    # 1) Direct bind attempts
    for dn in _build_dn_candidates(username, base_dn, people_dn):
        try:
            conn = Connection(server, user=dn, password=password, auto_bind=True, receive_timeout=5)
            conn.unbind()
            return {"dn": dn, "email": None, "display_name": short_username}
        except LDAPBindError:
            continue
        except LDAPException as exc:
            _log(logger, "warning", f"LDAP bind attempt failed for {dn}: {exc}")

    # 2) Admin bind + search, then verify with the found DN
    if admin_dn and admin_password and base_dn:
        try:
            admin_conn = Connection(server, user=admin_dn, password=admin_password,
                                     auto_bind=True, receive_timeout=5)
            admin_conn.check_names = False
        except LDAPException as exc:
            _log(logger, "error", f"LDAP admin bind failed: {exc}")
            return None

        search_filter = (
            "(|"
            f"(uid={username})(cn={username})(mail={username})(sAMAccountName={username})"
            f"(uid={short_username})(cn={short_username})(sAMAccountName={short_username})"
            ")"
        )
        try:
            admin_conn.search(
                search_base=base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=["mail", "displayName", "cn"],
            )
            entries = admin_conn.entries or []
            if not entries:
                return None
            entry = entries[0]
            user_dn = entry.entry_dn
            email = getattr(entry, "mail", None)
            email = email.value if email else None
            display_name = getattr(entry, "displayName", None)
            display_name = display_name.value if display_name else short_username

            try:
                user_conn = Connection(server, user=user_dn, password=password,
                                        auto_bind=True, receive_timeout=5)
                user_conn.unbind()
                return {"dn": user_dn, "email": email, "display_name": display_name}
            except LDAPException as exc:
                _log(logger, "warning", f"LDAP bind failed for discovered DN {user_dn}: {exc}")
        finally:
            try:
                admin_conn.unbind()
            except Exception:
                pass

    return None
