# Phase 02 测试实例隔离与套件转绿（当前阶段）

## 目标与范围

Phase 01 结束时 `uv run pytest` 在所有文档里都被写成"通过"，但从**未在集成环境里实跑过**：
本轮真实执行暴露了三个互相叠加的缺陷（破坏共享库、应用角色无 TRUNCATE、所有权假设）。
本阶段的目标是把 B4 的测试套件变成**可安全、可重复运行**的，并给出真实数字。

范围外：B4 的业务能力（Phase 01 已实现）、契约改动（本轮未改 `MAIN/contracts/**` 与
`contracts.lock`）。

## 完成条件

1. `uv run pytest` 在**模块本地实例**上从零可跑，且不再触碰共享的平台库/代理。
2. 三个真实缺陷修复，且每个都有可复现的失败证据与修复说明。
3. 套件真实转绿，数字写进本文件与 `docs/runbooks/local-development.md`。

## 缺陷与修复

| # | 缺陷 | 影响（已实测） | 修复 |
|---|---|---|---|
| 1 | `tests/conftest.py` 的 `clean_database` 是 `autouse` 且对 `b4.*` 逐表 `TRUNCATE`，而 DSN 来自 `B4/.env` → **平台库 55432** | 一次 `uv run pytest` 把集成环境的 `b4.users` / `b4.roles` / `b4.sections` / `b4.enrollments` / `b4.credentials` / `b4.roster_imports` 全部清空；`b4.users` 变成 0 行，登录返回 401，而现象与"种子没跑过"完全一样 | 新增模块本地实例（`B4/compose.yaml`：PostgreSQL **55435**、NATS **54224**，`B4/infra/postgres/init.sql` 建 `autograder_b4` 角色与 `b4` schema）；`tests/conftest.py` 在 **import 期**把 `DATABASE_URL` / `B4_ADMIN_DATABASE_URL` / `NATS_URL` 钉到本地实例（`B4_TEST_*` 可覆盖）。`load_environment_file()` 不覆盖已存在的键，所以钉住的值一定赢 |
| 2 | `clean_database` 用应用角色 `autograder_b4` 执行 `TRUNCATE` | `permission denied for table audit_records`（应用角色只有 SELECT/INSERT/UPDATE/DELETE，没有 TRUNCATE —— 这是**正确**的授权，泄漏的请求处理器不应该能清表） | 夹具改用特权连接 `admin_engine` 执行 TRUNCATE 与探针表清理；应用角色继续只用于断言。这正是 conftest 文档里"特权 DSN 只用于布置夹具状态"的原意 |
| 3 | `test_checker_detects_a_misnamed_index` 在 `b4.users` 上 `CREATE INDEX` | `must be owner of table users`（表属迁移角色，应用角色按 `RQ-B-4-10` 不拥有任何表） | 改为在测试自建的 `b4.probe_indexed` 上建索引：被验证的规则是"索引名不合 `ix_<table>_<cols>` 会被报告"，与挂在哪张表上无关 |
| 4 | 未迁移的测试库上直接跑套件 | 夹具里抛出裸 `relation "b4.audit_records" does not exist`，没有任何"该先跑迁移"的线索 | 新增会话级 `migrated_test_instance` 守卫：缺 `b4.users` 时 `pytest.fail` 并打印可直接粘贴的四条命令 |

### 顺带修复的源码缺陷

| # | 缺陷 | 影响 | 修复 |
|---|---|---|---|
| 5 | `plugin._context_environ(context)` 把宿主的设置视图**整体替换** `os.environ` | 空视图（`_NullContext.settings = {}`，即"没有宿主"）被判为"环境为空" → `Settings.from_environment({})` 报 `DATABASE_URL is required`，而环境完全有效；`tests/test_health.py::test_module_health_fails_when_it_cannot_initialize` 因此失败 | 视图改为**叠加**在 `os.environ` 之上，空视图等价于"没有宿主"。宿主给的视图仍然优先（ADR-0008 语义不变） |

## 验证（真实执行）

前置：`docker compose -f B4/compose.yaml -p b4 up -d`，然后

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://autograder_platform:change-me-local-only@127.0.0.1:55435/autograder'
uv run alembic upgrade head
uv run python -m autograder_b4.ops.provision
```

随后（不设任何 `DATABASE_URL`，让 conftest 的钉住值生效）：

```text
uv run pytest -q
209 passed, 2 warnings in 130.25s (0:02:10)   exit 0
```

对照修复前的同一条命令：

```text
uv run pytest -q                 # 指向平台库 55432
6 failed, 86 passed, 2 warnings, 117 errors in 92.18s   exit 1
# 且此后 b4.users = 0 行，登录 401
```

`alembic upgrade head`（本地实例，从零）：

```text
INFO  [alembic.runtime.migration] Running upgrade  -> 20260915_0001
INFO  [alembic.runtime.migration] Running upgrade 20260915_0001 -> 20260915_0002, Create the B4 platform tables.
```

`ops.provision`：

```text
provisioned role autograder_b4 on schema b4: 16 steps
role autograder_b4 is least privilege: no b2/b3 access, no superuser attributes
```

## 初期管理员引导（本阶段补齐，Phase 01 曾记为 `待实现`）

`src/autograder_b4/ops/bootstrap_admin.py` 已实现并被本轮真实使用：

```powershell
$env:B4_BOOTSTRAP_ADMIN_PASSWORD = '<local-only>'
uv run python -m autograder_b4.ops.bootstrap_admin --username admin --display-name "平台管理员"
```

```text
administrator 'admin' created (user_id=5fadc2cf-018e-4e6c-81d4-356b95372ddf)
```

幂等：同用户名重跑为 no-op，仅在缺少凭据时补写；口令来自 `--password` 或
`B4_BOOTSTRAP_ADMIN_PASSWORD`，不回显、不入日志；走与登录端点同一套 Argon2id 代码路径。

## 未完成 / 阻塞

- 无 `阻塞` 项。
- `contracts.lock` 与 `MAIN/contracts/**` **本轮未改动**（无 CHG）。
- 契约缺口 3/4/5（`RosterImportPreview` 描述与属性名不一致、无法表达自由文本说明、
  没有"给已有课程追加教师/助教"的 operation 或端点）**仍然存在**，需 MAIN 走变更规程；
  B4 继续用运维命令 `ops.roles grant` 承担第 5 条，不新增路径。
