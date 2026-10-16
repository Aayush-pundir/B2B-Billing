"""Shared fixtures: one isolated app + database per test session (no network, no real data)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update({"DATABASE_URL": os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db", "UPLOAD_DIR": f"{_tmp}/up", "DISABLE_SCHEDULER": "1", "JWT_SECRET": "test-secret"})
os.environ.pop("USAGE_DATABASE_URL", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client_factory():
    clients = []

    def make(role: str, email: str | None = None) -> TestClient:
        c = TestClient(app)
        r = c.post("/auth/dev-login", json={"email": email or f"{role}@test.local", "role": role})
        assert r.status_code == 200, r.text
        clients.append(c)
        return c

    with TestClient(app):  # runs startup (seeding, migrations)
        yield make


@pytest.fixture(scope="session")
def fin(client_factory):
    return client_factory("finance")


@pytest.fixture(scope="session")
def fin2(client_factory):
    return client_factory("finance", "finance2@test.local")


@pytest.fixture(scope="session")
def acc(client_factory):
    return client_factory("accounts")


@pytest.fixture(scope="session")
def adm(client_factory):
    return client_factory("admin")


@pytest.fixture(scope="session")
def sales_user(client_factory):
    return client_factory("sales")
