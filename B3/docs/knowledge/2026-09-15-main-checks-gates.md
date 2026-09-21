# MAIN `checks.py` 的两类门禁：废弃术语与本地端口漂移

- **现象**：
  1. `python -m autograder_host.checks` 报 `B3/alembic/versions/...py: deprecated B3 scoring artifact resurfaced (<term>)`；
  2. 清掉源码里的词之后，同一门禁改报 `B3/alembic/versions/__pycache__/*.pyc: ... resurfaced in compiled bytecode`；
  3. 另一类：`B3/.env.example` 或 `B3/alembic.ini` 报 `PostgreSQL host port 55434 does not match compose (55432)`。
- **重复触发条件**：
  1. 在 `B3/src`、`B3/alembic`、`B3/module.yaml` 里**任何位置**写下被禁术语——**包括"这里没有旧 X"这种叙述**，检查器是纯字符串扫描；
  2. 删掉源码但没删 `__pycache__`（历史事故：废弃设计以字节码形式存活）；
  3. 为了让测试用独立数据库，把 `.env.example` / `alembic.ini` 的端口改成模块本地库端口，而 MAIN 的门禁要求这两个文件与 **MAIN 自己的** `compose.yaml` 端口一致。
- **根因**：
  1. `check_deprecated_b3_artifacts()` 的判定是"实现面零出现"，不是"语义判定"；
  2. 它先检查 `.pyc` 内容、再跳过 `__pycache__` 目录里的其它文件——所以字节码**是**被检查的；
  3. `check_local_configuration_consistency()` 用 `MAIN/compose.yaml` 的宿主端口作为唯一基准，`.env.example`/`alembic.ini` 是"集成目标"，不是"模块本地库"。
- **最终修复**：
  1. 改写措辞使被禁术语零出现（中性表述："B3 不拥有评分/证据/报表对象"）；
  2. 清理全部 `__pycache__`：
     ```powershell
     Get-ChildItem -Recurse -Directory -Filter __pycache__ | Where-Object { $_.FullName -notlike '*\.venv\*' } | Remove-Item -Recurse -Force
     ```
  3. 端口分工写清：`.env.example`/`alembic.ini` = 平台端口（PostgreSQL 55432 / NATS 54222）；`B3/compose.yaml` + 测试默认值 = 模块本地库 55434，并在两处文件里互相注明。
- **预防措施**：
  - 改完实现面先跑 MAIN 门禁，不要只跑 `ruff`/`pytest`；
  - 迁移/模块 docstring 里**不要**复述被禁术语；
  - `.env.example` 端口改动前先读 `MAIN/compose.yaml`；
  - B3 侧同类断言：`tests/test_hygiene.py::test_no_legacy_identifier_anywhere`、`tests/test_migrations.py::test_no_legacy_tables_anywhere`。
- **关联证据**：
  - `MAIN/host/src/autograder_host/checks.py`（`check_deprecated_b3_artifacts` line 104-163、`check_local_configuration_consistency` line 240-303）
  - [../phases/active/phase-01-question-bank-and-packages.md](../phases/active/phase-01-question-bank-and-packages.md) 的"过程中被门禁拦下并修正的两件事"
  - [../runbooks/troubleshooting.md](../runbooks/troubleshooting.md) 第 4、5 条
