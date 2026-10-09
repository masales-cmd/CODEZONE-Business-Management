from logging.config import fileConfig
from pathlib import Path
import sys

_backend=Path(__file__).resolve().parents[1]
if str(_backend) not in sys.path:
    sys.path.insert(0,str(_backend))
from alembic import context
from sqlalchemy import engine_from_config,pool
from app.core.config import settings
from app.models.models import *
from app.core.database import Base
config=context.config
config.set_main_option('sqlalchemy.url',settings.database_url.replace('%','%%'))
if config.config_file_name: fileConfig(config.config_file_name)
target_metadata=Base.metadata
def run_migrations_offline():
    context.configure(url=settings.database_url,target_metadata=target_metadata,literal_binds=True,compare_type=True)
    with context.begin_transaction(): context.run_migrations()
def run_migrations_online():
    connectable=engine_from_config(config.get_section(config.config_ini_section,{}),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
