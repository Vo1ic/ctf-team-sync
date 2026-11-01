import os

from alembic import context
from sqlalchemy import create_engine

from team_sync.models import Base

target_metadata = Base.metadata
database_url = os.environ["TEAM_SYNC_DATABASE_URL"]

if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
