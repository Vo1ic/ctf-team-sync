"""Isolated PostgreSQL-backed server for browser tests or an explicit local preview."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from team_sync.api import create_app
from team_sync.settings import Settings


def main():
    url = os.environ["TEAM_SYNC_TEST_DATABASE_URL"]
    schema = "web_test_" + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_url = scoped.render_as_string(hide_password=False)
    try:
        os.environ["TEAM_SYNC_DATABASE_URL"] = scoped_url
        command.upgrade(Config("alembic.ini"), "head")
        with TemporaryDirectory(prefix="team-sync-web-") as directory:
            app = create_app(Settings(scoped_url, "browser-test-bootstrap-secret", Path(directory)))
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=int(os.getenv("TEAM_SYNC_WEB_TEST_PORT", "8941")),
                log_level="warning",
            )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


if __name__ == "__main__":
    main()
