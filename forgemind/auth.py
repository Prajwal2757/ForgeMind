"""Passwords, users and session tokens.

A token only identifies a user. Role, groups and active state are read from the database on
every request, so changes and revocations apply immediately.
"""
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

import jwt

ROLES = ("viewer", "editor", "admin")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}")
MIN_PASSWORD, MAX_PASSWORD = 12, 128


@dataclass(frozen=True)
class Principal:
    tenant: str
    user: str
    role: str
    groups: tuple

    def can_write(self) -> bool:
        return self.role in ("editor", "admin")

    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=5)
    return f"{salt.hex()}${key.hex()}"


def check_password(password: str, stored: str) -> bool:
    salt, key = stored.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=5)
    return hmac.compare_digest(actual.hex(), key)


_DUMMY_HASH = hash_password(secrets.token_hex(16))


def _validate(role=None, groups=None, password=None):
    if role is not None and role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    if groups is not None and not (isinstance(groups, (list, tuple))
                                   and all(isinstance(g, str) and NAME.fullmatch(g) for g in groups)):
        raise ValueError("groups must be a list of simple names")
    if password is not None and not (isinstance(password, str)
                                     and MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        raise ValueError(f"password must be {MIN_PASSWORD}-{MAX_PASSWORD} characters")


class Users:
    def __init__(self, db):
        self.db = db

    def create(self, tenant, name, password, role="viewer", groups=()):
        for value in (tenant, name):
            if not (isinstance(value, str) and NAME.fullmatch(value)):
                raise ValueError("tenant and user names: letters, digits, . _ @ - (max 64)")
        if password is None or role is None or groups is None:
            raise ValueError("password, role and groups cannot be null")
        _validate(role, groups, password)
        hashed = hash_password(password)
        with self.db.tx(write=True) as c:
            if c.execute("SELECT 1 FROM users WHERE tenant=? AND name=?", (tenant, name)).fetchone():
                raise ValueError("user already exists")
            c.execute("INSERT INTO users VALUES(?,?,?,?,?,1,0,?)",
                      (tenant, name, hashed, role, json.dumps(sorted(set(groups))), time.time()))

    def update(self, tenant, name, role=None, groups=None, password=None, active=None,
               expect_version=None):
        """Any change signs the user out. Refuses to remove the last active admin."""
        _validate(role, groups, password)
        if active is not None and not isinstance(active, bool):
            raise ValueError("active must be true or false")
        hashed = hash_password(password) if password is not None else None
        with self.db.tx(write=True) as c:
            row = c.execute("SELECT * FROM users WHERE tenant=? AND name=?", (tenant, name)).fetchone()
            if row is None:
                raise KeyError(name)
            if expect_version is not None and row["token_version"] != expect_version:
                raise ValueError("the account changed; sign in again")
            new_role = role or row["role"]
            new_active = row["active"] if active is None else int(active)
            if row["role"] == "admin" and row["active"] and (new_role != "admin" or not new_active):
                admins = c.execute("SELECT COUNT(*) FROM users WHERE tenant=? AND role='admin' "
                                   "AND active=1", (tenant,)).fetchone()[0]
                if admins <= 1:
                    raise ValueError("cannot remove the last active admin")
            c.execute("UPDATE users SET role=?, groups=?, password=?, active=?, "
                      "token_version=token_version+1 WHERE tenant=? AND name=?",
                      (new_role,
                       row["groups"] if groups is None else json.dumps(sorted(set(groups))),
                       hashed or row["password"], new_active, tenant, name))

    def list(self, tenant):
        with self.db.tx() as c:
            rows = c.execute("SELECT name,role,groups,active FROM users WHERE tenant=? ORDER BY name",
                             (tenant,)).fetchall()
        return [{"name": r["name"], "role": r["role"], "groups": json.loads(r["groups"]),
                 "active": bool(r["active"])} for r in rows]

    def _row(self, tenant, name):
        with self.db.tx() as c:
            return c.execute("SELECT * FROM users WHERE tenant=? AND name=?",
                             (tenant, name)).fetchone()

    def get(self, tenant, name):
        """The user's current principal, or None if unknown or disabled."""
        row = self._row(tenant, name)
        if row is None or not row["active"]:
            return None
        return Principal(tenant, name, row["role"], tuple(json.loads(row["groups"])))

    def verify_password(self, tenant, name, password):
        """Returns the account version the password was checked against, or None."""
        row = self._row(tenant, name)
        # Hash even for unknown users so timing does not reveal which names exist.
        ok = check_password(password, row["password"] if row else _DUMMY_HASH)
        return row["token_version"] if ok and row and row["active"] else None

    def tenant_exists(self, tenant) -> bool:
        with self.db.tx() as c:
            return c.execute("SELECT 1 FROM users WHERE tenant=?", (tenant,)).fetchone() is not None

    def issue_token(self, secret, tenant, name, ttl=3600, version=None) -> str:
        row = self._row(tenant, name)
        if row is None or not row["active"]:
            raise KeyError(name)
        now = int(time.time())
        return jwt.encode({"tenant": tenant, "sub": name, "ver": row["token_version"] if version is None else version,
                           "iat": now, "exp": now + ttl}, secret, algorithm="HS256")

    def authenticate(self, secret, token) -> Principal:
        """Raises jwt.InvalidTokenError for bad, expired or revoked tokens."""
        claims = jwt.decode(token, secret, algorithms=["HS256"],
                            options={"require": ["exp", "sub", "tenant", "ver"]})
        if not isinstance(claims["tenant"], str) or not isinstance(claims["sub"], str):
            raise jwt.InvalidTokenError("malformed claims")
        row = self._row(claims["tenant"], claims["sub"])
        if row is None or not row["active"] or row["token_version"] != claims["ver"]:
            raise jwt.InvalidTokenError("revoked")
        return Principal(claims["tenant"], claims["sub"], row["role"],
                         tuple(json.loads(row["groups"])))

    def revoke(self, tenant, name):
        with self.db.tx(write=True) as c:
            c.execute("UPDATE users SET token_version=token_version+1 WHERE tenant=? AND name=?",
                      (tenant, name))
