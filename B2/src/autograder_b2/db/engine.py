"""数据库连接入口（B2 只连 `b2` schema 所在实例；不读 `b3`/`b4` schema）。"""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy import Engine, create_engine

DATABASE_URL_ENV = "DATABASE_URL"


class DatabaseConfigurationError(RuntimeError):
    """缺少数据库连接配置（环境缺失，不是业务失败）。"""


def database_url_from_env(environ: Mapping[str, str] | None = None) -> str:
    """从环境读取连接串（`DATABASE_URL`）。缺失即明确报错，不做隐式默认。"""
    source = os.environ if environ is None else environ
    url = source.get(DATABASE_URL_ENV, "").strip()
    if not url:
        raise DatabaseConfigurationError(f"{DATABASE_URL_ENV} is not set")
    return url


def create_db_engine(url: str) -> Engine:
    """创建引擎：带 pre_ping（连接被中间件断开时自动重连），不做隐式 DDL。"""
    return create_engine(url, pool_pre_ping=True)
