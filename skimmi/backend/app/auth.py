"""Who is making the request: verifies Supabase Auth access tokens.

Sign-up, sign-in, sessions and password recovery are handled by Supabase Auth in
the frontend (supabase-js). The frontend sends the session's access token (a JWT)
as `Authorization: Bearer <token>`, and every protected route depends on
`current_user`, which verifies it here, independently of the frontend:

* the signature: with the project's public signing keys (JWKS, ES256/RS256), or
  with the legacy shared secret (HS256) if SUPABASE_JWT_SECRET is set;
* the expiry, the audience ("authenticated") and the issuer (this project);
* that it belongs to a signed-in user (role "authenticated", a UUID subject).

The user id is only ever taken from the verified token, never from the request.
Tokens are never logged.
"""

import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request

from . import config, database, ratelimit

log = logging.getLogger("skimmi.auth")

ASYMMETRIC_ALGORITHMS = ("ES256", "RS256")
LEEWAY_SECONDS = 10  # clock skew allowed on the expiry


@dataclass(frozen=True)
class User:
    id: str
    email: str = ""


class AuthError(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.message = message
        self.status = status


@lru_cache(maxsize=1)
def _jwks_client():
    # Keys are fetched once and cached; an unknown key id triggers one refetch
    # (Supabase rotates keys by publishing the new one first).
    return jwt.PyJWKClient(
        f"{config.SUPABASE_URL}/auth/v1/.well-known/jwks.json",
        cache_keys=True,
        lifespan=600,
        timeout=config.HTTP_TIMEOUT,
    )


def _signing_key(token):
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError:
        raise AuthError("Your session isn't valid. Sign in again.")

    if algorithm == "HS256" and config.SUPABASE_JWT_SECRET:
        return config.SUPABASE_JWT_SECRET, algorithm
    if algorithm in ASYMMETRIC_ALGORITHMS and config.SUPABASE_URL:
        try:
            return _jwks_client().get_signing_key_from_jwt(token).key, algorithm
        except jwt.PyJWKClientConnectionError:
            log.warning("Couldn't fetch the Supabase signing keys")
            raise AuthError("Couldn't check your session right now. Try again in a moment.", 503)
        except jwt.PyJWTError:
            raise AuthError("Your session isn't valid. Sign in again.")
    # Anything else, including "none" and HS256 without a configured secret.
    raise AuthError("Your session isn't valid. Sign in again.")


def verify_token(token):
    """The user a Supabase access token belongs to. Raises AuthError."""
    if not config.SUPABASE_URL:
        log.error("SUPABASE_URL is not set, so no session can be verified")
        raise AuthError("Sign-in isn't configured on the server.", 503)

    key, algorithm = _signing_key(token)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=config.JWT_AUDIENCE,
            issuer=f"{config.SUPABASE_URL}/auth/v1",
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Your session has expired. Sign in again.")
    except jwt.PyJWTError:
        raise AuthError("Your session isn't valid. Sign in again.")

    user_id = database.valid_id(claims.get("sub"))
    if claims.get("role") != "authenticated" or not user_id:
        raise AuthError("Your session isn't valid. Sign in again.")
    return User(id=user_id, email=str(claims.get("email") or ""))


# Profiles are created on a user's first request and "last seen" is refreshed at
# most every few minutes, not on every request.
_PROFILE_REFRESH_SECONDS = 600
_profiles_seen = {}
_profiles_lock = threading.Lock()


def _remember_profile(user):
    now = time.monotonic()
    with _profiles_lock:
        if now - _profiles_seen.get(user.id, -_PROFILE_REFRESH_SECONDS) < _PROFILE_REFRESH_SECONDS:
            return
        _profiles_seen[user.id] = now
    try:
        database.ensure_profile(user.id, user.email)
    except Exception:
        with _profiles_lock:
            _profiles_seen.pop(user.id, None)
        raise


def forget_profiles():
    """Tests: the next request creates the profile again."""
    with _profiles_lock:
        _profiles_seen.clear()


def client_ip(request):
    return request.client.host if request.client else "unknown"


def current_user(request: Request) -> User:
    """FastAPI dependency: the signed-in user, or 401. Repeated failures from one
    address are slowed down with 429 (see config.RATE_LIMITS["auth_failure"])."""
    ip = client_ip(request)
    ratelimit.check_blocked("auth_failure", ip)

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    try:
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthError("Sign in to continue.")
        user = verify_token(token.strip())
    except AuthError as e:
        if e.status == 401:
            ratelimit.hit("auth_failure", ip)
        raise HTTPException(e.status, e.message, headers={"WWW-Authenticate": "Bearer"} if e.status == 401 else None)

    _remember_profile(user)
    request.state.user_id = user.id  # for logging
    return user
