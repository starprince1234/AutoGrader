"""真实 PostgreSQL 测试夹具（共享；SQLite 不得用于数据库验收）。

数据库来源：`B2_TEST_DATABASE_URL` 或 `DATABASE_URL`。两者都没有时相关测试 **skip 并说明原因**
（不伪报通过）。每个测试模块拿到一个**临时专用库**，用完即删——绝不改动既有数据。
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

B2_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 本环境的系统临时根目录不可访问（`D:\dev\temp\pytest-of-*` 触发 WinError 5），
# 且工作区才是可写范围。把 pytest/`tempfile` 的临时根固定到工作区内：测试因此自洽
# （不依赖宿主临时目录的权限），也不污染仓库（.pytest-tmp/ 已 gitignore）。
_LOCAL_TEMP_ROOT = B2_ROOT / ".pytest-tmp"
_LOCAL_TEMP_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(_LOCAL_TEMP_ROOT)

ADMIN_URL = os.getenv("B2_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")

requires_postgres = pytest.mark.skipif(
    not ADMIN_URL,
    reason="需要真实 PostgreSQL：设置 B2_TEST_DATABASE_URL（不接受 SQLite 作为数据库验收）",
)


def run_alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """以固定参数数组调用 alembic CLI（无 shell 拼接、无 shell=True）。"""
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=B2_ROOT,
        # 强制 UTF-8 双向：Alembic 在离线模式会把迁移 docstring 作为注释回显到 stdout，
        # 而 Windows 控制台默认是 GBK（cp936）——遇到 GBK 之外的字符（如 `⇒`）子进程会直接
        # UnicodeEncodeError 退出（已实测）。固定编码使工具不再受控制台代码页影响。
        env={**os.environ, "DATABASE_URL": url, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


@contextmanager
def temporary_database() -> Iterator[str]:
    """建一个**空**库并返回其连接串，退出时删除（每个模块/用例各自独立）。"""
    assert ADMIN_URL is not None
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    name = f"b2_test_{uuid.uuid4().hex[:12]}"
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target = make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)
    try:
        yield target
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def fresh_database_url() -> Iterator[str]:
    """模块级空库（迁移一次、多个用例复用）。"""
    with temporary_database() as url:
        yield url


@pytest.fixture()
def unmigrated_database_url() -> Iterator[str]:
    """**未迁移**的空库（函数级，用完即删）。

    单独需要它是因为 `fresh_database_url` 是模块级的：同一模块里只要有用例触发过
    `upgraded_database_url`，那个库就已经在 head 上了，无法再用来验证"连得上但未初始化"。
    """
    with temporary_database() as url:
        yield url


@pytest.fixture(scope="module")
def upgraded_database_url(fresh_database_url: str) -> str:
    """把空库迁移到 head，供仓储/存储层测试使用。"""
    result = run_alembic(fresh_database_url, "upgrade", "head")
    assert result.returncode == 0, f"upgrade head 失败: {result.stderr[-1500:]}"
    return fresh_database_url


@pytest.fixture()
def engine(upgraded_database_url: str) -> Iterator[Engine]:
    """每个用例开始前清空 `b2` 全部业务表。

    为什么：临时库是**模块级**的（迁移一次、多个用例复用），如果不清空，"计数"类断言
    （如"存储条目数 == 1"）就会依赖用例执行顺序。清空使每个用例从确定的初始状态出发。

    表清单**从数据库派生**而不是写死：新增迁移后无需再回来补这一行（曾因漏掉 `outbox_events`
    导致跨用例串数据）。版本表 `alembic_version_b2` 不动（迁移状态保持）。
    """
    instance = create_engine(upgraded_database_url)
    try:
        with instance.begin() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'b2' AND table_type = 'BASE TABLE'"
                    "   AND table_name <> 'alembic_version_b2'"
                )
            ).fetchall()
            tables = ", ".join(f'b2."{row[0]}"' for row in rows)
            if tables:
                connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        yield instance
    finally:
        instance.dispose()


@pytest.fixture()
def artifact_root(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root
