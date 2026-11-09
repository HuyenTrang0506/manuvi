"""Login, roles, sessions and login rate limiting (stdlib only).

Users come from env ``MANUVI_WEB_USERS``: a JSON list ``[{"user": "...", "role": "founder|reviewer", "hash": "scrypt$..."}]``
(an SSM-backed loader can replace ``load_users`` later). Hash format:
``scrypt$<log2 N>$<r>$<p>$<salt b64>$<hash b64>``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

ROLES = ("founder", "reviewer")
SESSION_COOKIE = "manuvi_session"
IDLE_SECONDS = 8 * 3600
MAX_SECONDS = 12 * 3600
MIN_LOG_N = 10
DEFAULT_LOG_N = 15


class AuthConfigError(Exception):
    pass


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _scrypt(password: str, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    n = 1 << log_n
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=256 * n * r, dklen=32)


def hash_password(password: str, log_n: int = DEFAULT_LOG_N, r: int = 8, p: int = 1) -> str:
    if len(password) < 8:
        raise ValueError("password must have at least 8 characters")
    salt = secrets.token_bytes(16)
    return f"scrypt${log_n}${r}${p}${_b64(salt)}${_b64(_scrypt(password, salt, log_n, r, p))}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, log_n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt" or not (MIN_LOG_N <= int(log_n) <= 20):
            return False
        got = _scrypt(password, base64.b64decode(salt), int(log_n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class User:
    name: str
    role: str
    hash: str


def load_users(env: Optional[Mapping[str, str]] = None) -> dict[str, User]:
    env = os.environ if env is None else env
    raw = env.get("MANUVI_WEB_USERS", "").strip()
    if not raw:
        raise AuthConfigError("MANUVI_WEB_USERS is not set: login is mandatory, no anonymous access")
    try:
        items = json.loads(raw)
        users = {i["user"]: User(i["user"], i["role"], i["hash"]) for i in items}
    except (ValueError, KeyError, TypeError):
        raise AuthConfigError("MANUVI_WEB_USERS must be a JSON list of {user, role, hash}")
    if not users or any(u.role not in ROLES for u in users.values()):
        raise AuthConfigError("MANUVI_WEB_USERS: roles must be founder or reviewer")
    if not any(u.role == "founder" for u in users.values()):
        raise AuthConfigError("MANUVI_WEB_USERS: at least one founder is required")
    for u in users.values():
        if not u.name or u.hash.split("$")[0] != "scrypt":
            raise AuthConfigError("MANUVI_WEB_USERS: bad user entry")
    return users


@dataclass
class Session:
    user: str
    role: str
    csrf: str
    created: float
    last: float


class RateLimiter:
    """Failed logins, two layers:

    * hard: ``max_fails`` within ``window`` seconds locks that (ip, user) key and that ip's total (3x) for
      ``lockout`` seconds. Success clears the user key.
    * soft, per user name across all addresses (a botnet or a rotating proxy defeats the per-address layer):
      from the ``soft_after``-th failure the account answers "wait" for ``base_delay * 2**(n - soft_after)``
      seconds after the last failure, capped at ``max_delay``. It never becomes a permanent lock, so an attacker
      cannot lock the owner out for longer than ``max_delay``.
    """

    MAX_KEYS = 5000

    def __init__(self, max_fails: int = 5, window: float = 900, lockout: float = 900,
                 clock: Callable[[], float] = time.time, soft_after: int = 5, base_delay: float = 2.0,
                 max_delay: float = 300.0):
        self.max_fails, self.window, self.lockout, self.clock = max_fails, window, lockout, clock
        self.soft_after, self.base_delay, self.max_delay = soft_after, base_delay, max_delay
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _name(user: str) -> str:
        return user.strip().lower()[:80]

    def _recent(self, key: str) -> list[float]:
        now = self.clock()
        v = [t for t in self.fails.get(key, []) if now - t < self.window]
        self.fails[key] = v
        return v

    def _prune(self) -> None:
        if len(self.fails) <= self.MAX_KEYS:
            return
        for k in [k for k in self.fails if not self._recent(k)]:  # expired entries first
            del self.fails[k]
        for k in sorted(self.fails, key=lambda k: self.fails[k][-1])[:max(0, len(self.fails) - self.MAX_KEYS)]:
            del self.fails[k]  # still too many (random user names): drop the oldest

    def retry_after(self, ip: str, user: str) -> int:
        u = self._name(user)
        with self.lock:
            now = self.clock()
            worst = 0.0
            for key, limit in ((f"{ip}|{u}", self.max_fails), (f"{ip}|*", self.max_fails * 3)):
                v = self._recent(key)
                if len(v) >= limit:
                    worst = max(worst, v[-1] + self.lockout - now)
            v = self._recent(f"*|{u}")
            if len(v) >= self.soft_after:
                delay = min(self.base_delay * 2 ** (len(v) - self.soft_after), self.max_delay)
                worst = max(worst, v[-1] + delay - now)
            return max(0, int(worst) + 1) if worst > 0 else 0

    def fail(self, ip: str, user: str) -> None:
        u = self._name(user)
        with self.lock:
            for key in (f"{ip}|{u}", f"{ip}|*", f"*|{u}"):
                self._recent(key).append(self.clock())
            self._prune()

    def success(self, ip: str, user: str) -> None:
        u = self._name(user)
        with self.lock:
            self.fails.pop(f"{ip}|{u}", None)
            self.fails.pop(f"*|{u}", None)


class Auth:
    def __init__(self, users: dict[str, User], limiter: Optional[RateLimiter] = None,
                 clock: Callable[[], float] = time.time):
        self.users, self.clock = users, clock
        self.limiter = limiter or RateLimiter(clock=clock)
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        # Unknown user names must cost as much as known ones: reuse the cost parameters of a real hash.
        params = next((u.hash.split("$")[1:4] for u in users.values()), [str(DEFAULT_LOG_N), "8", "1"])
        try:
            self._dummy = hash_password("not-a-real-password", log_n=int(params[0]), r=int(params[1]), p=int(params[2]))
        except (ValueError, IndexError):
            self._dummy = hash_password("not-a-real-password")

    def login(self, ip: str, name: str, password: str) -> tuple[Optional[str], int]:
        """Returns (session id, 0) on success; (None, retry_after_seconds) otherwise (0 = bad credentials)."""
        wait = self.limiter.retry_after(ip, name)
        if wait:
            return None, wait
        user = self.users.get(name)
        ok = verify_password(password, user.hash if user else self._dummy) and user is not None
        if not ok:
            self.limiter.fail(ip, name)
            return None, 0
        self.limiter.success(ip, name)
        sid = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            self.sessions[sid] = Session(user.name, user.role, secrets.token_urlsafe(24), now, now)
        return sid, 0

    def get(self, sid: Optional[str]) -> Optional[Session]:
        if not sid:
            return None
        with self.lock:
            s = self.sessions.get(sid)
            if s is None:
                return None
            now = self.clock()
            if now - s.last > IDLE_SECONDS or now - s.created > MAX_SECONDS:
                del self.sessions[sid]
                return None
            s.last = now
            return s

    def logout(self, sid: Optional[str]) -> None:
        with self.lock:
            self.sessions.pop(sid or "", None)
