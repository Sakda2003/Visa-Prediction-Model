import asyncio
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging

import bcrypt
import jwt
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from backend.app import main
from backend.app.api import auth as auth_api
from backend.app.api import dependencies as auth_dependencies
from backend.app.api.dependencies import require_roles
from backend.app.core.config import settings
from backend.app.core.errors import APIError

TEST_JWT_SECRET = "fastapi-auth-test-secret-with-32-bytes"


class FakeUsersCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []

    async def find_one(self, query: dict[str, str]) -> dict[str, object] | None:
        for document in self.documents:
            matches = True
            for key, value in query.items():
                actual = document.get(key)
                if isinstance(value, dict) and "$in" in value:
                    matches = actual in value["$in"]
                elif isinstance(value, dict) and "$regex" in value:
                    import re

                    matches = bool(
                        re.fullmatch(
                            str(value["$regex"]).removeprefix("^").removesuffix("$"),
                            str(actual or ""),
                            re.IGNORECASE,
                        )
                    )
                else:
                    matches = actual == value
                if not matches:
                    break
            if matches:
                return document
        return None

    async def insert_one(self, document: dict[str, object]) -> None:
        if any(existing["_id"] == document["_id"] for existing in self.documents):
            raise DuplicateKeyError("duplicate _id")
        self.documents.append(document.copy())


class FakeVisaTypesCollection:
    def find(self, _query: dict[str, object]) -> "FakeVisaTypesCollection":
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, object]]:
        assert length is None
        return []


class FakeDatabase:
    def __init__(self) -> None:
        self.users = FakeUsersCollection()
        self.visa_types = FakeVisaTypesCollection()

    def __getitem__(self, collection_name: str) -> object:
        if collection_name == "users":
            return self.users
        if collection_name == "visatypes":
            return self.visa_types
        raise AssertionError(f"Unexpected collection: {collection_name}")


class FakeMongoClient:
    def __init__(self) -> None:
        self.database = FakeDatabase()
        self.closed = False
        self.admin = self

    def get_database(self, _database_name: str) -> FakeDatabase:
        return self.database

    async def command(self, _command: str) -> dict[str, int]:
        return {"ok": 1}

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def auth_client(monkeypatch) -> Iterator[tuple[TestClient, FakeDatabase]]:
    mongo_client = FakeMongoClient()
    test_settings = replace(settings, jwt_secret=TEST_JWT_SECRET)
    main.rate_limiter.clear()
    monkeypatch.setattr(main, "create_mongo_client", lambda: mongo_client)
    monkeypatch.setattr(auth_api, "settings", test_settings)
    monkeypatch.setattr(auth_dependencies, "settings", test_settings)
    with TestClient(main.app) as client:
        yield client, mongo_client.database
    assert mongo_client.closed


def test_registration_persists_a_customer_and_returns_compatible_token(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = auth_client
    response = client.post(
        "/api/auth/register",
        json={
            "name": "  Test User ",
            "email": " Test@Example.com ",
            "password": "Password123",
            "role": "ADMIN",
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["message"] == "User registered successfully"
    assert result["user"]["name"] == "Test User"
    assert result["user"]["email"] == "test@example.com"
    assert result["user"]["role"] == "CUSTOMER"
    assert "password" not in result["user"]
    stored_user = database.users.documents[0]
    assert bcrypt.checkpw(
        b"Password123", str(stored_user["password"]).encode("ascii")
    )

    claims = jwt.decode(result["token"], TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["id"] == stored_user["_id"]
    assert claims["email"] == "test@example.com"
    assert claims["role"] == "CUSTOMER"


def test_registration_rejects_missing_fields_and_duplicate_email(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = auth_client

    missing = client.post("/api/auth/register", json={"email": "user@example.com"})
    assert missing.status_code == 400
    assert missing.json() == {
        "message": "Name, email, and password are required"
    }

    payload = {
        "name": "Test User",
        "email": "user@example.com",
        "password": "Password123",
    }
    assert client.post("/api/auth/register", json=payload).status_code == 201
    duplicate = client.post(
        "/api/auth/register",
        json={**payload, "email": "USER@example.com"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {"message": "User already exists"}


def test_login_and_current_user_hide_password_hash(
    auth_client: tuple[TestClient, FakeDatabase],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=auth_api.__name__)
    client, _database = auth_client
    client.post(
        "/api/auth/register",
        json={
            "name": "Test User",
            "email": "user@example.com",
            "password": "Password123",
        },
    )

    incorrect_password = client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "IncorrectPassword"},
    )
    assert incorrect_password.status_code == 401
    assert incorrect_password.json() == {"message": "Invalid credentials"}

    login = client.post(
        "/api/auth/login",
        json={"email": "USER@example.com", "password": "Password123"},
    )
    assert login.status_code == 200
    assert login.json()["message"] == "Login successful"
    assert "Login request received" in caplog.text
    assert "Login rejected: password verification failed" in caplog.text
    assert "Login password verification succeeded" in caplog.text
    assert "Login JWT generation succeeded" in caplog.text
    assert "IncorrectPassword" not in caplog.text
    assert "Password123" not in caplog.text
    assert "user@example.com" not in caplog.text
    token = login.json()["token"]

    current_user = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert current_user.status_code == 200
    assert current_user.json()["user"]["email"] == "user@example.com"
    assert "password" not in current_user.json()["user"]


def test_authentication_errors_return_401_without_exposing_token_details(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = auth_client

    missing = client.get("/api/auth/me")
    assert missing.status_code == 401
    assert missing.json() == {"message": "Authentication required"}

    invalid = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-token"}
    )
    assert invalid.status_code == 401
    assert invalid.json() == {"message": "Invalid or expired token"}

    expired = jwt.encode(
        {
            "id": "1",
            "email": "user@example.com",
            "role": "CUSTOMER",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )
    expired_response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert expired_response.status_code == 401

    login = client.post(
        "/api/auth/login",
        json={"email": "missing@example.com", "password": "Password123"},
    )
    assert login.status_code == 401
    assert login.json() == {"message": "Invalid credentials"}

    malformed = client.post("/api/auth/register", json={"email": []})
    assert malformed.status_code == 400
    assert malformed.json() == {"message": "Validation error"}
    not_found = client.get("/api/not-a-route")
    assert not_found.status_code == 404
    assert not_found.json() == {"message": "Resource not found"}


def test_legacy_mongo_user_can_login_case_insensitively_without_hash_leak(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = auth_client
    object_id = ObjectId()
    database.users.documents.append(
        {
            "_id": object_id,
            "name": "Legacy Admin",
            "email": "Legacy.Admin@example.com",
            "passwordHash": bcrypt.hashpw(
                b"Password123", bcrypt.gensalt()
            ).decode("ascii"),
            "role": "admin",
        }
    )
    duplicate = client.post(
        "/api/auth/register",
        json={
            "name": "Duplicate User",
            "email": "legacy.admin@example.com",
            "password": "AnotherPassword123",
        },
    )
    assert duplicate.status_code == 409
    assert len(database.users.documents) == 1

    login = client.post(
        "/api/auth/login",
        json={"email": "  LEGACY.ADMIN@EXAMPLE.COM ", "password": "Password123"},
    )
    assert login.status_code == 200
    result = login.json()
    assert result["user"]["_id"] == str(object_id)
    assert result["user"]["role"] == "ADMIN"
    assert "passwordHash" not in result["user"]
    claims = jwt.decode(result["token"], TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["role"] == "ADMIN"

    profile = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {result['token']}"},
    )
    assert profile.status_code == 200
    assert profile.json()["user"]["_id"] == str(object_id)
    assert "passwordHash" not in profile.json()["user"]


def test_visa_type_reads_require_auth_and_role_restrictions_remain_enabled(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = auth_client
    admin_token = auth_api._sign_token(
        {"_id": "admin-1", "email": "admin@example.com", "role": "admin"}
    )
    staff_token = auth_api._sign_token(
        {"_id": "staff-1", "email": "staff@example.com", "role": "STAFF"}
    )
    customer_token = auth_api._sign_token(
        {"_id": "customer-1", "email": "customer@example.com", "role": "CUSTOMER"}
    )
    invalid_role_token = jwt.encode(
        {
            "id": "unknown-1",
            "email": "unknown@example.com",
            "role": "SUPERUSER",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        TEST_JWT_SECRET,
        algorithm="HS256",
    )

    assert client.get("/api/visa-types").status_code == 401
    for path, token in (
        ("/api/visa-types", admin_token),
        ("/api/visa-types/", staff_token),
        ("/api/visa-types/", customer_token),
    ):
        response = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"visaTypes": [], "count": 0}

    restricted = client.post(
        "/api/visa-types",
        json={},
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert restricted.status_code == 403
    invalid_role = client.get(
        "/api/visa-types",
        headers={"Authorization": f"Bearer {invalid_role_token}"},
    )
    assert invalid_role.status_code == 403


def test_login_rate_limit_matches_express_attempt_limit(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, _database = auth_client
    payload = {"email": "missing@example.com", "password": "Password123"}

    responses = [
        client.post("/api/auth/login", json=payload) for _ in range(10)
    ]
    assert all(response.status_code == 401 for response in responses)

    limited = client.post("/api/auth/login", json=payload)
    assert limited.status_code == 429
    assert limited.text == "Too many requests, please try again later."


def test_role_guard_rejects_customer_for_admin_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dependency = require_roles("ADMIN")
    caplog.set_level(logging.INFO, logger=auth_dependencies.__name__)

    async def check() -> None:
        await dependency(
            principal={
                "id": "1",
                "email": "user@example.com",
                "role": "CUSTOMER",
            }
        )

    with pytest.raises(APIError, match="Insufficient permissions"):
        asyncio.run(check())
    assert "Role authorization denied for role=CUSTOMER" in caplog.text


def test_inactive_account_is_rejected_without_changing_user_record(
    auth_client: tuple[TestClient, FakeDatabase],
) -> None:
    client, database = auth_client
    account = {
        "_id": "inactive-admin",
        "email": "inactive@example.com",
        "password": bcrypt.hashpw(b"Password123", bcrypt.gensalt()).decode("ascii"),
        "role": "ADMIN",
        "status": "INACTIVE",
    }
    database.users.documents.append(account.copy())

    response = client.post(
        "/api/auth/login",
        json={"email": "inactive@example.com", "password": "Password123"},
    )

    assert response.status_code == 403
    assert response.json() == {"message": "Account is not active"}
    assert database.users.documents == [account]


def test_role_guard_accepts_case_insensitive_admin_and_staff_roles() -> None:
    for role, allowed_roles in (
        ("admin", ("ADMIN",)),
        ("staff", ("ADMIN", "STAFF")),
    ):
        dependency = require_roles(*allowed_roles)
        principal = {
            "id": "1",
            "email": "user@example.com",
            "role": role,
        }
        assert asyncio.run(dependency(principal=principal)) == principal
