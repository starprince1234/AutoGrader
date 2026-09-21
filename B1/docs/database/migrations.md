# B1 迁移说明

## 结论：本模块无迁移

B1 是纯静态前端，**没有数据库，没有 Alembic 迁移，没有 schema**。

## 为什么没有迁移

1. B1 不拥有任何权威数据（`module.yaml`：`database.schema: none`）。
2. B1 不连接 PostgreSQL；数据读写全部经 B4/B3/B2 的 HTTP 接口完成。
3. `module.yaml` 的 `migration_head: none` 与"无迁移"保持一致，MAIN 校验（`migration_head` 一致性）应通过。

## 迁移约定（仅作背景参考，非 B1 责任）

Alembic 约定（每模块独立版本表、一次迁移一件事、可回滚性、head 与 `module.yaml` 一致）见 `../../../MAIN/docs/requirements/rq-catalog.md` RQ-B-4-30 与 `../../../MAIN/docs/database/ownership.md`。B1 **不适用**这些约定。

## 若未来引入本地"数据结构"版本

若将来为浏览器草稿结构引入版本化（例如草稿 schema 升级），这属于**前端本地格式**，不是数据库迁移，且必须：

- 在 `../adr/` 新增决策记录；
- 在本目录 `schema.md` 更新"能存什么"；
- 明确向前兼容与清理策略。

当前无此需求。
