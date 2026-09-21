# 数据库迁移：不适用

## 1. 声明

B5 **没有 Alembic 目录、没有迁移版本、没有版本表**。[module.yaml](../../module.yaml) 的
`database.migration_head` 是字符串 `none`——这与 B1 的写法一致（同样是"无权威数据库"的模块），
也是清单 schema 允许的取值。

## 2. 为什么这里不是"待补"

[MAIN 的所有权目录](../../../MAIN/docs/database/ownership.md) 规定"物理表结构由各模块自己的迁移
定义"。一个没有表的模块没有迁移可写：写一个空的 `alembic/versions/` 只会让 MAIN 的门禁
（`check_local_configuration_consistency()` 对 schema 所有者校验"恰有一个 head，且与清单一致"）
多一个需要照顾的对象，而它对任何事实都不构成证据。

因此没有迁移目录是**结论**，不是留白。

## 3. 与 MAIN 门禁的关系

`check_local_configuration_consistency()` 只遍历 `module_schema_owners()`；B5 声明
`schema: none`，所以：

- 不参与"迁移 head 与 `module.yaml` 一致"的校验；
- 不参与 `.env.example` / `alembic.ini` 的端口一致性校验（B5 的 `.env.example` 里没有任何
  数据库或 NATS 端口，因为它不连接它们）。

## 4. 如果将来 B5 需要保存东西

顺序是固定的，且**先有决定，再有代码**：

1. 新 ADR 说明"为什么按需报告不够"（这是不可逆决策：一旦有了第二份事实源，就要维护它的新鲜度）；
2. 走 [CHG 流程](../../../MAIN/docs/contracts/change-protocol.md) 与
   [所有权目录](../../../MAIN/docs/database/ownership.md) 申请 schema 归属；
3. 同步 `module.yaml` 的 `database.schema` / `migration_head`、本文件、`schema.md` 与
   `docs/README.md` 状态表；
4. 之后才建 `alembic/` 与第一份迁移。

在此之前，任何"先建个空目录占位"的做法都不会被采纳。
