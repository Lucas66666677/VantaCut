from __future__ import annotations

import uuid

import jwt

from app.core.config import settings
from app.core.security import create_access_token

VALID_PASSWORD = "correct horse battery staple"


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:12]}@example.com"


def test_register_creates_user_and_returns_token(client):
    response = client.post(
        "/api/v1/auth/register", json={"email": _unique_email(), "password": VALID_PASSWORD}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_register_duplicate_email_rejected(client):
    payload = {"email": _unique_email(), "password": VALID_PASSWORD}
    first = client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 201
    second = client.post("/api/v1/auth/register", json=payload)
    assert second.status_code == 400


def test_register_rejects_password_below_minimum_length(client):
    response = client.post("/api/v1/auth/register", json={"email": _unique_email(), "password": "short"})
    assert response.status_code == 422


def test_register_rejects_malformed_email(client):
    response = client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": VALID_PASSWORD}
    )
    assert response.status_code == 422


def test_login_succeeds_with_correct_credentials(client):
    email = _unique_email()
    client.post("/api/v1/auth/register", json={"email": email, "password": VALID_PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": VALID_PASSWORD})
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_login_wrong_password_rejected(client):
    email = _unique_email()
    client.post("/api/v1/auth/register", json={"email": email, "password": VALID_PASSWORD})
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "definitely wrong"})
    assert response.status_code == 401


def test_login_unknown_email_rejected(client):
    response = client.post(
        "/api/v1/auth/login", json={"email": _unique_email(), "password": VALID_PASSWORD}
    )
    assert response.status_code == 401


def test_me_rejects_anonymous_request(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_rejects_malformed_token(client):
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_me_rejects_token_for_nonexistent_user(client):
    fake_token = create_access_token(uuid.uuid4())
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fake_token}"})
    assert response.status_code == 401


def test_me_rejects_expired_token(client):
    email = _unique_email()
    register_response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": VALID_PASSWORD}
    )
    user_id = uuid.UUID(
        jwt.decode(
            register_response.json()["access_token"], settings.jwt_secret_key, algorithms=["HS256"]
        )["sub"]
    )
    # Mint a token already expired instead of sleeping past a real TTL.
    expired_token = create_access_token(user_id, expires_minutes=-1)
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


def test_me_succeeds_with_valid_token(client):
    email = _unique_email()
    register_response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": VALID_PASSWORD}
    )
    token = register_response.json()["access_token"]
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["is_active"] is True


def test_password_never_returned_in_any_auth_response(client):
    email = _unique_email()
    register_response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": VALID_PASSWORD}
    )
    token = register_response.json()["access_token"]
    me_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    for response in (register_response, me_response):
        assert VALID_PASSWORD not in response.text
        assert "hashed_password" not in response.text
