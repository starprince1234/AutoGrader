# B3 故障排查（Troubleshooting）

只收录**已观察到**或**可确定**的问题；不编造。症状 → 可能原因 → 处置 → 确认恢复。

## 1. `uv` 报"无法写缓存目录"（Phase 00 曾观察到；2026-09-15 已不复现）

- 症状：`uv sync` / `uv run` 报 cache 目录（`D:\dev\caches\uv`）写入失败 / EPERM。
- 可能原因：沙箱把 uv 缓存目录隔离在工作区之外，禁止写。
- 处置：把 `UV_CACHE_DIR` 指向工作区内目录，或在可写缓存的机器（Linux）执行。
- 确认恢复：`uv sync --all-groups` 正常完成。当前环境 `uv 0.10.2` 下已正常（见 [Phase 01](../phases/active/phase-01-question-bank-and-packages.md)）。

## 2. 契约摘要漂移（可确定）

- 症状：MAIN `checks.py` 报 B3 `contracts.lock` 摘要与实时 `contracts/` 摘要不一致。
- 可能原因：`MAIN/contracts/**` 变更后未重算摘要，或有人手动改了锁值。
- 处置：**不硬改锁**。走 `../../../MAIN/docs/contracts/change-protocol.md`，由 MAIN 重算并一次性更新四个模块锁。
- 确认恢复：`checks.py` 通过（正常输出含 `contracts, manifests and requirements: ok (<摘要>)`）。

## 3. 端口绑定失败 / 连不上数据库（已观察到）

- 症状 A：`docker compose up` 报 `ports are not available: exposing port TCP 0.0.0.0:5432 ... bind: An attempt was made to access a socket in a way forbidden by its permissions`。
  - 原因：本机 `5432` 落在系统保留/排除端口区间。
  - 处置：用非保留端口。平台库是 **55432**（MAIN）、模块本地库是 **55434**（B3），NATS 是 **54222**。
- 症状 B：`connection refused`。
  - 原因：PostgreSQL 未启动，或 `DATABASE_URL` 未覆盖 `alembic.ini` 的占位值，或端口写错（55432 与 55434 混用）。
  - 处置：`docker compose up -d postgres`；显式 `export/set DATABASE_URL`；对照 [local-development.md](local-development.md) §0 的两套数据库表。
- 症状 C：`alembic upgrade head` 报 `permission denied to create schema b3`。
  - 原因：用最小权限角色（`_app`/`_internal`）执行迁移。
  - 处置：迁移必须用 schema owner（模块本地库 `autograder_b3`，平台库 `autograder_platform`）。
- 确认恢复：`uv run alembic heads` 显示 `20260915_0002`。

## 4. MAIN 门禁报"deprecated B3 scoring artifact resurfaced"（已观察到两次）

- 症状：`checks.py` 在 `B3/alembic/versions/*.py`（第一次）或 `B3/alembic/versions/__pycache__/*.pyc`（第二次）上报实现面出现废弃评分术语。
- 原因：该门禁对 `B3/src`、`B3/alembic`、`B3/module.yaml`（**含 `__pycache__` 里的 `.pyc`**）要求这些术语**零出现**——连"这里没有旧 X"这种叙述也算命中；且删除源码不会删除字节码。
- 处置：改写措辞使其不含被禁词；并清理 `__pycache__`：
  ```powershell
  Get-ChildItem -Recurse -Directory -Filter __pycache__ | Where-Object { $_.FullName -notlike '*\.venv\*' } | Remove-Item -Recurse -Force
  ```
- 确认恢复：`checks.py` 输出 `contracts, manifests and requirements: ok`。

## 5. MAIN 门禁报"local configuration drift"（已观察到）

- 症状：`B3/.env.example` 或 `B3/alembic.ini` 的 PostgreSQL/NATS 宿主端口与 `MAIN/compose.yaml` 不一致。
- 原因：为使用模块本地库把端口改成了 55434 / 54222 之外的值。
- 处置：这两个文件必须写**平台**端口（PostgreSQL 55432、NATS 54222）；模块本地库端口只出现在 `B3/compose.yaml`、runbook 与测试默认值里，并在文件中注明分工。
- 确认恢复：`checks.py` 输出 `migration heads and local ports agree with compose: ok`。

## 6. 测试被跳过并提示 PostgreSQL 不可达

- 症状：`uv run pytest` 大量 skip，理由为 `PostgreSQL at ... is unreachable`。
- 原因：模块本地库没起，或 55434 被别的服务占用。
- 处置：`docker compose up -d postgres`；或用 `B3_TEST_OWNER_URL`/`B3_TEST_APP_URL`/`B3_TEST_INTERNAL_URL` 指向其它实例（该实例需已存在两个最小权限角色，否则权限断言会失败而不是跳过）。
- 确认恢复：`282 passed`，无 skip。

## 7. 发布返回 `409`，但"内容是新的"（可确定）

- 症状：`POST /question-versions/{id}/publish` 返回 `409 conflict`。
- 可能原因（按概率）：
  1. 该版本**不是草稿**（已发布/已归档）——只有草稿可发布；
  2. 编译出的 `package_digest` 与发布时记录的不一致（受保护内容在发布后被改动）——响应 `detail` 会说明；
  3. 归档动作重复调用。
- 处置：用 `GET /api/v1/question-versions/{id}` 看 `status`；若要改内容，用 `PATCH /api/v1/questions/{id}?as_new_version=true` 建新版本而不是改旧版本（旧版本不可变，这是设计要求）。
- 确认恢复：新版本 `publish` 返回 `200`，`content_digest` 与 `package_digest` 均为 64 位十六进制。

## 8. 事件消费"没有统计"（可确定）

- 症状：评测已完成但题库统计一直是 0。
- 可能原因与判定：
  1. **事件被拒收**：查 `autograder_b3.events.consumer` 的 `evaluation.completed.rejected` ERROR 日志——`reason` 会指出原因（非法生产者 / 越界字段 / 时间戳无时区）。被拒事件**不会 ack**，会在 DLQ 里等待人工处理，这是设计行为。
  2. **NATS 未配置**：`NATS_URL` 为空时统计消费根本不启动（同步 API 仍工作），`consumer_cli` 会明确报错。
  3. **重复**：日志 `evaluation.completed.duplicate` 表明该 `event_id`/`evaluation_run_id` 已处理过，属正常去重。
  4. **`is_pass` 字段名不符**：白名单不含未知字段，事件会被拒收 → 见 [CHG-B3-0003](../contracts/changes/CHG-B3-0003-distinct-student-statistic.md)。
- 处置：修复上游 payload 或用 `uv run python -m autograder_b3.consumer_cli --rebuild` 从 inbox 重建统计。
- 确认恢复：`GET /api/v1/questions/{id}/statistics` 的 `attempt_count` 增长。

## 9. 事件声明方向不一致（SDK 已知缺口，可确定）

- 症状：`descriptor.events` 只列 `question.version.published`，未声明订阅 `evaluation.completed`；宿主无法校验 publishes/subscribes。
- 可能原因：SDK `ModuleDescriptor.events` 是扁平元组（`../../../MAIN/docs/adr/ADR-0003` 遗留风险）。
- 处置：本模块内部**以 `module.yaml` 为准**；SDK 修复需走契约变更规程（P3 破坏性变更）。不要为了"看起来一致"而发明第二套约定——那会变成兼容层。
- 确认恢复：SDK 改为 `publishes`/`subscribes` 后，`plugin.py` 与 `module.yaml` 三方一致。

## 10. 公开响应疑似含受保护字段（可确定）

- 症状：TC-QST-01 失败，公开 DTO 出现受保护字段。
- 可能原因：公开/内部 DTO 未物理分离，序列化器默认导出全部字段，或路由直接返回了 ORM 对象/内部 DTO。
- 处置：核对 [../architecture/boundaries.md](../architecture/boundaries.md) §1 与 `ADR-0004`；公开响应必须经 `serializers.serialize_public`（它会**抛错**而不是静默丢弃）；`dto/public.py` 不得含受保护词汇。
- 确认恢复：`tests/test_dto_boundary.py` 与 `tests/test_questions_api.py` 全绿。

## 11. 受保护表读取报 `permission denied`（这是预期的）

- 症状：用最小权限角色（`DATABASE_URL`）读 `b3.reference_answers` 等表时报 `permission denied`。
- 原因：这是**设计**（验收 3-10-3）——普通题库角色对 5 张受保护表被 `REVOKE ALL`。
- 处置：需要读受保护内容时**只能**走内部路径：`DATABASE_PROTECTED_URL`（发布/编译路径）或服务间 `evaluation-package` 端点。不要在业务代码里换角色绕过。
- 确认恢复：`tests/test_migrations.py::test_app_role_cannot_read_protected_tables` 通过（即"该拒绝仍然生效"）。
