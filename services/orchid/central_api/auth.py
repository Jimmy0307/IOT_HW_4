"""Minimal local-lab AuthN/AuthZ: PBKDF2 password hashes, short-lived HS256 JWT, 2 roles.

viewer   : read only
operator : read + manual override + control commands
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

import jwt

ISSUER = "orchid-central-api"
AUDIENCE = "orchid-dashboard"
ROLES = ("viewer", "operator")
WEB_PBKDF2_ITERATIONS = 210000  # must match scripts/bootstrap_secrets.py
_DUMMY_HASH = (f"pbkdf2_sha256${WEB_PBKDF2_ITERATIONS}$" + base64.b64encode(b"0" * 16).decode() + "$"
               + base64.b64encode(b"0" * 32).decode())
MAX_LIMITER_KEYS = 10000


@dataclass(frozen=True)
class Principal:
    username: str
    role: str


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iterations))
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


class UserStore:
    def __init__(self, users: dict[str, dict[str, str]]) -> None:
        self._users = {u: v for u, v in users.items() if v.get("role") in ROLES}

    def authenticate(self, username: str, password: str) -> Principal | None:
        user = self._users.get(username)
        # Constant-ish work for unknown users (avoid trivial user enumeration by timing).
        ok = verify_password(password, user["password_hash"] if user else _DUMMY_HASH)
        if user and ok:
            return Principal(username, user["role"])
        return None


class TokenService:
    def __init__(self, secret: str, ttl_seconds: int, algorithm: str = "HS256") -> None:
        if len(secret) < 32:
            raise ValueError("JWT secret too short")
        self._secret = secret
        self.ttl = ttl_seconds
        self.algorithm = algorithm

    def issue(self, principal: Principal) -> str:
        now = int(time.time())
        claims = {"sub": principal.username, "role": principal.role, "iat": now, "nbf": now,
                  "exp": now + self.ttl, "jti": str(uuid.uuid4()), "iss": ISSUER, "aud": AUDIENCE}
        return jwt.encode(claims, self._secret, algorithm=self.algorithm)

    def verify(self, token: str) -> Principal | None:
        try:
            claims: dict[str, Any] = jwt.decode(
                token, self._secret, algorithms=[self.algorithm], audience=AUDIENCE, issuer=ISSUER,
                options={"require": ["exp", "iat", "sub", "role", "iss", "aud"]})
        except jwt.PyJWTError:
            return None
        if claims.get("role") not in ROLES:
            return None
        return Principal(str(claims["sub"]), str(claims["role"]))


class LoginRateLimiter:
    """Sliding 60 s window per (client, username) and per client."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def _purge(self, now: float) -> None:
        for key in [k for k, w in self._hits.items() if not w or now - w[-1] > 60]:
            del self._hits[key]

    def allow(self, *keys: str) -> bool:
        """At capacity, expired windows are purged; if still full, NEW keys are refused (fail closed).
        Existing windows are never evicted, so a key spray cannot reset an attacked key's counter."""
        now = time.monotonic()
        allowed = True
        for key in keys:
            window = self._hits.get(key)
            if window is None:
                if len(self._hits) >= MAX_LIMITER_KEYS:
                    self._purge(now)
                if len(self._hits) >= MAX_LIMITER_KEYS:
                    allowed = False
                    continue
                window = self._hits[key] = deque()
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= self.per_minute:
                allowed = False
            window.append(now)
        return allowed
