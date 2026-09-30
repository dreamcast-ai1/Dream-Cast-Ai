from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (register tables)
from app.config import get_settings
from app.db import Base, make_engine

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)

url = get_settings().database_url
target_metadata = Base.metadata


def run_migrations_online():
    engine = make_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=url.startswith("sqlite"))
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
