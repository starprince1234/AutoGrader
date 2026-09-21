import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

B2_SCHEMA = "b2"
VERSION_TABLE = "alembic_version_b2"

config = context.config
if config.config_file_name and config.get_section("loggers"):
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")))
target_metadata = None


def _ensure_version_schema() -> None:
    """在 Alembic 建版本表之前确保其 schema 存在（空库从零迁移的前置条件）。

    为什么必须在这里做：Alembic 在**运行任何迁移之前**就要创建
    `b2.alembic_version_b2`，而创建 `b2` schema 的正是基线迁移
    `20260915_0001`。因此在真正的空库上（G-DB-04：空库可从零迁移到 head）
    会以 `InvalidSchemaName: schema "b2" does not exist` 失败——已实测。
    基线迁移是已发布历史，不允许改，故把 schema 前置创建放在这里。
    """
    context.execute(f"CREATE SCHEMA IF NOT EXISTS {B2_SCHEMA}")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        version_table_schema=B2_SCHEMA,
    )
    _ensure_version_schema()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {B2_SCHEMA}")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_schema=B2_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
