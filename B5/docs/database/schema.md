# 数据库：无权威数据库

## 1. 归属声明

**B5 没有数据库 schema，也不连接任何数据库。**

- [module.yaml](../../module.yaml)：`database.schema: none`、`database.migration_head: none`。
- MAIN 门禁的 `module_schema_owners()` 因此**不包含** B5（这正是 CHG-0010 §5.2 要求被证明的一条）。
- `src/autograder_b5/**` 中没有 SQLAlchemy、没有驱动、没有连接串、没有 DDL。

## 2. 为什么不是"暂时没有"

B5 的产物是**关于当前时刻的一个回答**，不是一份需要留存的事实：

| 若保存报告 | 后果 |
|---|---|
| 存进自己的 schema | 多一份会过期的"契约一致性"副本，而 MAIN 的静态门禁与 B4 的注册表才是权威。第二份事实源正是本模块要消灭的东西 |
| 存进别人的 schema | 违反 ADR-0011（跨模块只经 API/事件）与各模块的 schema 所有权 |
| 存成文件 | 引入"报告的新鲜度"这个第二问题：读的人必须判断文件是否是当前的。端点按需生成没有这个问题 |

因此"没有表"是一条**设计结论**，而不是骨架阶段的缺失。

## 3. 我读什么（唯一的持久化接触面）

| 对象 | 方式 | 是否缓存 |
|---|---|---|
| `MAIN/contracts/openapi/*.yaml` | 只读文件（`CONTRACTS_DIR`） | **不缓存**：报告必须反映当前磁盘上的契约。文件很小（KB 级），每请求重读是有意的正确性取舍 |
| B4 发布的 JWKS | 只读 HTTP | 进程内缓存 300 秒，按 URL 分键 |

两者都不是"我的数据"：前者属 MAIN，后者属 B4。B5 只是读者。

## 4. 生命周期与清理

- 无表、无行、无保留策略、无清理任务。
- 内存中的 JWKS 缓存随进程结束消失；没有后台线程、没有定时任务、没有 outbox。
- 因此本模块在 `/health/ready` 中不需要报告数据库或消息中间件状态——它不依赖它们。

## 5. 可核验动作

```powershell
cd MAIN
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'host/src'); from autograder_host import checks; print(checks.module_schema_owners()); print(checks.discovered_modules())"
```

期望：第一行 `['B2', 'B3', 'B4']`（不含 `B5`），第二行 `['B1', 'B2', 'B3', 'B4', 'B5']`。

```powershell
Get-ChildItem B5 -Recurse -Directory -Filter alembic   # 期望：没有输出
```
