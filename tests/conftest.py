import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from team_sync.api import create_app
from team_sync.settings import Settings


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("TEAM_SYNC_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEAM_SYNC_TEST_DATABASE_URL to a disposable PostgreSQL database")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_url = scoped_url.render_as_string(hide_password=False)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("TEAM_SYNC_DATABASE_URL", scoped_url)
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            command.upgrade(config, "head")
            yield scoped_url
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def app(database_url, tmp_path):
    return create_app(Settings(database_url, "test-bootstrap-secret", tmp_path / "artifacts"))


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        yield value


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def initialize(client, **kwargs):
    response = client.post(
        "/api/v1/projects",
        headers=headers("test-bootstrap-secret"),
        json={"name": "Test CTF", "owner_name": "Captain", **kwargs},
    )
    assert response.status_code == 201, response.text
    return response.json()


def enroll(client, project, name="Agent", role="worker"):
    response = client.post(
        f"/api/v1/projects/{project['project']['id']}/actors",
        headers=headers(project["token"]),
        json={"name": name, "role": role, "owner_id": project["actor"]["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def execute(client, project, operation, arguments=None, resource_id=None, token=None, key=None):
    return client.post(
        f"/api/v1/projects/{project['project']['id']}/commands",
        headers=headers(token or project["token"]),
        json={
            "operation": operation,
            "arguments": arguments or {},
            "resource_id": resource_id,
            "idempotency_key": key or str(uuid4()),
        },
    )


@pytest.fixture
def project(client):
    return initialize(client)
