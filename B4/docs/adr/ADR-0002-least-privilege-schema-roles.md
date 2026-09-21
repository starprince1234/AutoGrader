# ADR 0002 单实例多 schema 的最小权限角色

- **状态**：接受（已实现并实测，见 `../phases/active/phase-01-platform-implementation.md`）
- **背景**：
  - 系统采用单 PostgreSQL 实例多 schema（`b2`/`b3`/`b4`），物理表归各模块迁移（`../../MAIN/docs/adr/ADR-0001-plugin-composition-and-schema-ownership.md`）。
  - `RQ-B-4-10` 要求"每个模块使用独立最小权限 login role，仅拥有本 schema 权限；禁止应用使用超级用户"。
  - `MAIN/infra/postgres/init.sql` **只建 schema，无角色供给**（MAIN 侧文件，B4 不修改）。
- **决策**：
  1. 每个模块一个 **login role**（如 `autograder_b4`），只授予本 schema 的 DDL/DML 权限，不授予其他 schema 与实例级权限。
  2. 应用连接一律使用对应最小权限角色；**禁止超级用户**运行应用（启动时查 catalog 拒绝）。
  3. B4 负责实例/角色/schema 供给与健康，但**不代管** B2/B3 的物理表；跨 schema 引用只传 UUID。
  4. 禁用 `public` 共享 ORM、跨 schema 外键/视图/SQL。
- **实现补充（写下来以免只存在于代码里）**：
  - 角色属性固定为 `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 10`，并 `ALTER ROLE … SET search_path = b4, pg_catalog`。
  - 供给对 `b2`/`b3` 显式 `REVOKE ALL`（表与 schema 两级），使"只能访问 b4"由数据库保证而非约定。
  - **schema 创建属于供给、表创建属于迁移**：PostgreSQL 在执行 `CREATE SCHEMA` 时先校验 database 级
    `CREATE` 权限再处理 `IF NOT EXISTS`，因此裸的 `CREATE SCHEMA IF NOT EXISTS b4` 会让最小权限角色在
    迁移时失败（实测）；迁移 0001 因此改为存在性守卫。
  - 供给脚本的库名取自 admin DSN（不是第二个环境变量），因为角色是**集群级**对象：用管理员口令给第二个
    库供给会把该角色口令改掉，使既有部署认证失败（实测踩到）。
- **后果**：
  - 获得：一个模块的凭据泄漏不能越权读写他模块数据；越权在数据库层被拒绝（**已实测**：`SELECT b2.users`
    与 `CREATE TABLE b3.x` 均报 `permission denied for schema`）。
  - 付出：角色/授权矩阵与供给脚本需维护；迁移执行需区分"供给角色"与"模块迁移角色"。
  - 遗留风险：`public` 的 `CREATE` 权限已在供给中撤销；供给是否并入 MAIN 的初始化脚本属 MAIN 决策
    （当前为 B4 的独立入口，不修改 MAIN 文件）。
- **关联**：
  - 需求：`RQ-B-4-10`、`RQ-B-4-30`
  - 运行手册：`../runbooks/database-operations.md`（角色与 schema 供给）
  - 边界：`../architecture/boundaries.md`（数据边界）
  - MAIN：`../../MAIN/docs/database/ownership.md`、`../../MAIN/docs/adr/ADR-0001-*.md`
