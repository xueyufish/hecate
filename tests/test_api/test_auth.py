"""Tests for user authentication endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from hecate.services.auth.password import hash_password, verify_password
from hecate.services.auth.token import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)


class TestPasswordUtils:
    def test_hash_and_verify(self) -> None:
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed)
        assert not verify_password("wrong", hashed)

    def test_different_hashes(self) -> None:
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password("same", h1)
        assert verify_password("same", h2)


class TestTokenManagement:
    def test_create_and_decode_access_token(self) -> None:
        from uuid import uuid4

        user_id = uuid4()
        token = create_access_token(user_id)
        payload = decode_access_token(token)
        assert payload["sub"] == str(user_id)
        assert payload["type"] == "access"

    def test_create_and_decode_refresh_token(self) -> None:
        from uuid import uuid4

        user_id = uuid4()
        token = create_refresh_token(user_id)
        payload = decode_refresh_token(token)
        assert payload["sub"] == str(user_id)
        assert payload["type"] == "refresh"

    def test_access_token_rejected_as_refresh(self) -> None:
        from uuid import uuid4

        from jose import JWTError

        token = create_access_token(uuid4())
        with pytest.raises(JWTError, match="Not a refresh token"):
            decode_refresh_token(token)

    def test_refresh_token_rejected_as_access(self) -> None:
        from uuid import uuid4

        from jose import JWTError

        token = create_refresh_token(uuid4())
        with pytest.raises(JWTError, match="Not an access token"):
            decode_access_token(token)


@pytest.mark.usefixtures("setup_database")
class TestAuthAPI:
    async def test_register_success(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "securepass123"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "test@example.com"
        assert "id" in data

    async def test_register_duplicate_email(self, client: AsyncClient) -> None:
        await client.post(
            "/api/auth/register",
            json={"email": "dup@example.com", "password": "securepass123"},
        )
        resp = await client.post(
            "/api/auth/register",
            json={"email": "dup@example.com", "password": "otherpass456"},
        )
        assert resp.status_code == 409

    async def test_register_short_password(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/auth/register",
            json={"email": "short@example.com", "password": "123"},
        )
        assert resp.status_code == 422

    async def test_login_success(self, client: AsyncClient) -> None:
        await client.post(
            "/api/auth/register",
            json={"email": "login@example.com", "password": "securepass123"},
        )
        resp = await client.post(
            "/api/auth/login",
            json={"email": "login@example.com", "password": "securepass123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_wrong_password(self, client: AsyncClient) -> None:
        await client.post(
            "/api/auth/register",
            json={"email": "wrong@example.com", "password": "securepass123"},
        )
        resp = await client.post(
            "/api/auth/login",
            json={"email": "wrong@example.com", "password": "badpassword"},
        )
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/auth/login",
            json={"email": "nobody@example.com", "password": "securepass123"},
        )
        assert resp.status_code == 401

    async def test_refresh_token(self, client: AsyncClient) -> None:
        await client.post(
            "/api/auth/register",
            json={"email": "refresh@example.com", "password": "securepass123"},
        )
        login_resp = await client.post(
            "/api/auth/login",
            json={"email": "refresh@example.com", "password": "securepass123"},
        )
        refresh_token = login_resp.json()["refresh_token"]

        resp = await client.post(
            "/api/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_get_me(self, client: AsyncClient) -> None:
        await client.post(
            "/api/auth/register",
            json={"email": "me@example.com", "password": "securepass123"},
        )
        login_resp = await client.post(
            "/api/auth/login",
            json={"email": "me@example.com", "password": "securepass123"},
        )
        access_token = login_resp.json()["access_token"]

        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "me@example.com"

    async def test_get_me_unauthorized(self, client: AsyncClient) -> None:
        resp = await client.get("/api/auth/me")
        assert resp.status_code in (401, 403)
