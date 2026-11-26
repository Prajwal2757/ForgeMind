"""Signed tokens carrying tenant, user, role and groups."""
import time
from dataclasses import dataclass

import jwt

ROLES = ("viewer", "editor", "admin")


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


def issue_token(secret, tenant, user, role="viewer", groups=(), ttl=8 * 3600) -> str:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    now = int(time.time())
    claims = {"tenant": tenant, "sub": user, "role": role, "groups": list(groups),
              "iat": now, "exp": now + ttl}
    return jwt.encode(claims, secret, algorithm="HS256")


def verify_token(secret, token) -> Principal:
    """Raises jwt.InvalidTokenError on any bad, expired or incomplete token."""
    c = jwt.decode(token, secret, algorithms=["HS256"],
                   options={"require": ["exp", "sub", "tenant", "role"]})
    groups = c.get("groups", [])
    if c["role"] not in ROLES or not isinstance(c["tenant"], str) or not c["tenant"] \
            or not isinstance(groups, list) or not all(isinstance(g, str) for g in groups):
        raise jwt.InvalidTokenError("malformed claims")
    return Principal(c["tenant"], c["sub"], c["role"], tuple(groups))
