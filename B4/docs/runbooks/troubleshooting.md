# B4 故障排查手册

> 只收录**可确定或真实存在**的问题。格式：症状 → 可能原因 → 处置 → 确认恢复。
> 本阶段实现过程中真实踩到的问题也一并收录（标注「实测遇到」），否则同样的排查会重复一遍。

## 1. 本地连不上 PostgreSQL（端口）

- 症状：`uv run alembic upgrade head` 报连接 `localhost:5432` 失败。
- 可能原因：`alembic.ini` 的默认 URL 与宿主发布端口不一致。
- 处置：用 `DATABASE_URL` 覆盖为 `postgresql+psycopg://autograder_b4:<pw>@localhost:55432/autograder`。
- 确认：`docker compose ps`（`../../MAIN`）显示 postgres 健康，且 `alembic current` 能连上。

## 2. `alembic upgrade head` 退出码 0 但库里没有表（实测遇到）

- 症状：命令无报错、退出码 0，`b4` 下只有 `alembic_version_b4` 或什么都没有。
- 可能原因：`env.py` 在迁移前执行了 `set_config`，它开启了一个隐式事务；Alembic 因此把连接视为
  "外部事务"而**不提交**，整个迁移在连接关闭时被回滚。
- 处置：`set_config` 之后、`context.configure` 之前显式 `connection.commit()`（已修复，且在 `env.py`
  留了注释）。
- 确认：`SELECT version_num FROM b4.alembic_version_b4;` 返回预期 revision，且 `pg_tables` 计数 >1。

## 3. `CREATE SCHEMA IF NOT EXISTS b4` 被拒（`permission denied for database`）（实测遇到）

- 症状：应用角色执行 `alembic upgrade head` 时在 0001 处失败，报 database 级权限不足。
- 可能原因：PostgreSQL 先校验 database 级 `CREATE`，**再**看 `IF NOT EXISTS`——schema 已存在也照样报错。
- 处置：schema 创建属于**供给步骤**（`python -m autograder_b4.ops.provision`）；迁移 0001 改为存在性守卫。
- 确认：先供给、再迁移，`upgrade head` 作为应用角色成功。

## 4. 应用报 `password authentication failed for user "autograder_b4"`

- 症状：切换数据库或重跑供给后，原本可用的连接串突然认证失败。
- 可能原因：角色是**集群级**对象。用管理员口令对另一个库执行供给，会把该角色的口令改成管理员口令。
- 处置：供给时传应用角色自己的口令（`B4_DB_PASSWORD`）；库名取自 admin DSN 而非第二个变量。
- 确认：`uv run python -m autograder_b4.ops.provision --verify-only` 通过，且 `DATABASE_URL` 可连。

## 5. `init.sql` 建了 schema 但没有角色

- 症状：应用用 `autograder_b4` 连接报角色不存在或无权限。
- 可能原因：`../../MAIN/infra/postgres/init.sql` 只建 schema，无角色供给（MAIN 侧文件，本模块不修改）。
- 处置：执行 `uv run python -m autograder_b4.ops.provision`（幂等）。
- 确认：以 `autograder_b4` 能连接 `b4`，且对 `b2`/`b3` 的访问被数据库拒绝。

## 6. 非 PDF/XLSX 上传返回 500 而不是 422（实测遇到）

- 症状：上传 `.csv` 或扩展名与内容不符的文件，服务端抛异常。
- 可能原因：文档类型异常类曾是不可赋 `__traceback__` 的 frozen dataclass，且路由器未捕获它。
- 处置：异常类改为普通 `ValueError` 子类；`create_import` 捕获并转换为 `422`。
- 确认：`tests/test_roster_import.py::test_a_non_pdf_or_xlsx_upload_is_rejected` 通过。

## 7. NATS 已停止，readiness 仍报健康（实测遇到）

- 症状：`docker compose stop nats` 之后，readiness 仍然说 NATS 可达、健康。
- 可能原因：探针用裸 TCP connect，而容器运行时的端口转发在容器停止后**仍然接受连接**。
- 处置：探针改为读取 NATS 服务端 `INFO` 问候语（真实握手）。
- 确认：`uv run python -m autograder_b4.ops.outage_drill` 第 4 步报 `nats is unreachable …`；
  `tests/test_messaging.py::test_the_probe_does_not_trust_an_open_port` 作为回归用例。

## 8. 消息重复投递导致业务效果重复（实测遇到）

- 症状：同一 `event_id` 投递两次时，处理函数被调用两次。
- 可能原因：去重判定用了 `INSERT … ON CONFLICT DO NOTHING` 的 `rowcount`，而 psycopg3 + SQLAlchemy
  下该值恒为 `-1`，于是每次投递都被判为"重复"或"首次"逻辑反转。
- 处置：改用 `RETURNING` + `fetchone()`：冲突时不返回行，据此判定是否首次。
- 确认：`tests/test_messaging.py::test_a_duplicate_delivery_produces_one_business_effect`。

## 9. 名单确认全部被拒后，状态没有变成 `rejected`

- 症状：`POST …/confirm` 返回 `422`，但该导入仍是 `previewed`，审计里也没有失败记录。
- 可能原因：终态与审计写在事务内，`raise` 让事务整体回滚。
- 处置：先提交终态与审计，再抛错（登录限流、刷新重用撤销同属此类，一并修复）。
- 确认：`tests/test_roster_import.py::test_a_fully_rejected_roster_commits_nothing_and_is_terminal`。

## 10. 乐观并发不生效：同一秒内的第二次写入被接受（实测遇到）

- 症状：带过期的 `expected_updated_at` 仍然写入成功。
- 可能原因：写入与比较都把时间截断到整秒，同秒内的两次修改无法区分。
- 处置：统一到毫秒（`api/timestamps.py`），格式化与比较集中一处实现。
- 确认：`tests/test_users_courses.py::test_update_enrollment_honours_optimistic_concurrency`。

## 11. host 启动失败：`module.yaml` 与 descriptor 不一致

- 症状：host 启动即报 `RegistryError`，信息指明模块与字段。
- 可能原因：这正是设计行为——注册表在装配时校验漂移（字段不一致、路由重复、capability 无人提供、
  契约摘要不符）。
- 处置：先读错误信息定位是**哪一侧**该改；契约问题走
  `../../MAIN/docs/contracts/change-protocol.md`，**不要**为了启动而放宽校验。
- 确认：修正后 host 正常启动，`GET /api/v1/modules` 返回一致内容。

## 12. `descriptor.events` 与 `module.yaml` 对不上（方向）

- 症状：代码里 `events` 是单个扁平元组，`module.yaml` 却是 `publishes`/`subscribes` 两段。
- 可能原因：SDK `ModuleDescriptor.events` 是扁平元组（已知缺口，ADR-0003 遗留风险）。
- 处置：注册表据此只做**集合包含**校验（不得多、不得少），方向以 `module.yaml` 为准；**不**改
  `module.yaml` 或 `plugin.py` 去"自圆其说"。修复需 MAIN 的 SDK 破坏性变更。
- 确认：变更经 MAIN 评审并同步四模块后，方向可被机械校验。
