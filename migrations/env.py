from alembic import context
from app.database import make_engine, metadata

config = context.config
target_metadata = metadata

if context.is_offline_mode():
    raise RuntimeError('Offline migrations are not supported')

engine = make_engine()
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
