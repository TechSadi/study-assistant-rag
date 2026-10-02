"""Access tokens: every data route needs a valid Supabase session."""

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app import config, database
from app.main import app

from .conftest import JWT_SECRET, SUPABASE_URL, USER_A, auth_headers, make_pdf, make_token, upload

PROTECTED = [
    ("get", "/api/me"),
    ("get", "/api/documents"),
    ("post", "/api/documents"),
    ("patch", "/api/documents/11111111-1111-4111-8111-000000000000"),
    ("delete", "/api/documents/11111111-1111-4111-8111-000000000000"),
    ("post", "/api/documents/11111111-1111-4111-8111-000000000000/reindex"),
    ("get", "/api/documents/11111111-1111-4111-8111-000000000000/file"),
    ("get", "/api/conversations"),
    ("get", "/api/conversations/11111111-1111-4111-8111-000000000000"),
    ("delete", "/api/conversations/11111111-1111-4111-8111-000000000000"),
    ("post", "/api/chat"),
    ("post", "/api/messages/1/quiz-attempts"),
    ("get", "/api/progress"),
]


@pytest.mark.parametrize("method, path", PROTECTED)
def test_every_data_route_needs_a_session(anonymous, method, path):
    res = getattr(anonymous, method)(path)
    assert res.status_code == 401
    assert res.json() == {"detail": "Sign in to continue.", "code": "unauthorized"}
    assert res.headers["WWW-Authenticate"] == "Bearer"


def test_health_is_public_and_says_nothing_else(anonymous):
    res = anonymous.get("/api/health")
    assert res.status_code == 200 and res.json() == {"status": "ok"}


@pytest.mark.parametrize("token, message", [
    (make_token(exp=int(time.time()) - 60), "expired"),
    (make_token(secret="someone-elses-secret-that-is-also-long-enough"), "isn't valid"),
    (make_token(aud="anon"), "isn't valid"),
    (make_token(iss="https://evil.supabase.co/auth/v1"), "isn't valid"),
    (make_token(role="anon"), "isn't valid"),                  # the public anon key is not a user
    (make_token(user_id="not-a-uuid"), "isn't valid"),
    (make_token(exp=None), "isn't valid"),                     # no expiry
    (make_token(sub=None), "isn't valid"),
    (jwt.encode({"sub": USER_A, "role": "authenticated", "aud": "authenticated"}, None, algorithm="none"), "isn't valid"),
    ("not.a.jwt", "isn't valid"),
])
def test_invalid_tokens_are_rejected(anonymous, token, message):
    res = anonymous.get("/api/documents", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401
    assert message in res.json()["detail"]


def test_basic_auth_or_a_bare_token_is_rejected(anonymous):
    assert anonymous.get("/api/documents", headers={"Authorization": make_token()}).status_code == 401
    assert anonymous.get("/api/documents", headers={"Authorization": "Basic dXNlcjpwYXNz"}).status_code == 401


def test_hs256_is_refused_when_no_secret_is_configured(anonymous, monkeypatch):
    # A project on asymmetric signing keys must not accept tokens signed with a guessable secret.
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", "")
    assert anonymous.get("/api/documents", headers=auth_headers()).status_code == 401


def test_asymmetric_tokens_are_verified_with_the_projects_public_keys(anonymous, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    from app import auth

    key = ec.generate_private_key(ec.SECP256R1())
    other = ec.generate_private_key(ec.SECP256R1())

    class FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("Key", (), {"key": key.public_key()})()

    monkeypatch.setattr(auth, "_jwks_client", lambda: FakeJwks())
    claims = {"sub": USER_A, "role": "authenticated", "aud": "authenticated",
              "iss": f"{SUPABASE_URL}/auth/v1", "exp": int(time.time()) + 600}
    good = jwt.encode(claims, key, algorithm="ES256", headers={"kid": "k1"})
    forged = jwt.encode(claims, other, algorithm="ES256", headers={"kid": "k1"})

    assert anonymous.get("/api/documents", headers={"Authorization": f"Bearer {good}"}).status_code == 200
    assert anonymous.get("/api/documents", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_unreachable_signing_keys_are_a_503_not_a_401(anonymous, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ec

    from app import auth

    class DownJwks:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("down")

    monkeypatch.setattr(auth, "_jwks_client", lambda: DownJwks())
    token = jwt.encode({"sub": USER_A}, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256")
    res = anonymous.get("/api/documents", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 503  # the frontend shouldn't sign the user out for this


def test_first_request_creates_the_profile(client):
    body = client.get("/api/me").json()
    assert body["id"] == USER_A
    assert body["email"] == "1111@example.com"
    assert (body["documents"], body["conversations"], body["quiz_attempts"]) == (0, 0, 0)

    upload(client, "a.pdf", make_pdf(["Alpha"]))
    client.post("/api/chat", json={"question": "What?"})
    body = client.get("/api/me").json()
    assert (body["documents"], body["conversations"]) == (1, 1)


def test_repeated_bad_tokens_are_rate_limited(anonymous, monkeypatch):
    monkeypatch.setitem(config.RATE_LIMITS, "auth_failure", (3, 300))
    bad = {"Authorization": "Bearer nope"}
    assert [anonymous.get("/api/documents", headers=bad).status_code for _ in range(4)] == [401, 401, 401, 429]
    res = anonymous.get("/api/documents", headers=auth_headers())
    assert res.status_code == 429 and "Retry-After" in res.headers


def test_user_ids_in_the_request_are_ignored(client, other_client):
    # Only the token decides who you are; a user id in the body or query changes nothing.
    doc = upload(client, "a.pdf", make_pdf(["Alpha"])).json()
    assert other_client.get(f"/api/documents?user_id={USER_A}").json() == []
    res = other_client.post("/api/chat", json={"question": "Q", "doc_id": doc["id"], "user_id": USER_A})
    assert res.status_code in (400, 404)


def test_supabase_not_configured_is_a_503(anonymous, monkeypatch):
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    res = anonymous.get("/api/documents", headers={"Authorization": f"Bearer {make_token()}"})
    assert res.status_code == 503


def test_a_fresh_session_still_works_after_the_profile_cache_is_cleared(env, prompts):
    from app import auth

    fresh = TestClient(app, headers=auth_headers(USER_A))
    assert fresh.get("/api/documents").status_code == 200
    auth.forget_profiles()
    with database.begin() as conn:  # the profile already exists: no duplicate error
        assert conn.execute(database.profiles.select()).all()
    assert fresh.get("/api/documents").status_code == 200


def test_token_secret_is_not_in_any_response(client):
    body = client.get("/api/me").text + client.get("/api/health").text
    assert JWT_SECRET not in body
