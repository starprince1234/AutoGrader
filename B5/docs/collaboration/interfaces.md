# 接口约定（与 MAIN 及每个邻居）

## 1. 与 MAIN

| 问题 | 答案 |
|---|---|
| 我发布什么 | 一个端点：`GET /api/v1/platform/conformance`（只读） |
| 我消费什么 | `MAIN/contracts/openapi/*.yaml`（文件）、宿主进程的 `app.state.modules`（SDK 协议） |
| 我锁定哪个契约版本 | 见 [contracts.lock](../../contracts.lock)（**引用文件，不写死版本号**） |
| 摘要漂移时怎么办 | 停下，按 [契约变更规程](../../../MAIN/docs/contracts/change-protocol.md) 走 CHG；不自行改契约、不偷偷对齐实现 |
| 我要求 MAIN 做什么 | 只需 `MAIN/pyproject.toml` 的 `modules` extra 与 `[tool.uv.sources]` 各一行（CHG-0010 已裁定）。**host 代码、契约文件、其它模块共 0 字节改动** |
| 门禁关系 | 治理门禁自动纳入我（`glob("B*/module.yaml")`），我无需被登记 |

## 2. 与各邻居

| 邻居 | 我依赖它什么 | 它依赖我什么 | 失败时的行为 |
|---|---|---|---|
| B4 | 能力 `b4-auth`（装载顺序）；其发布的 JWKS 作为验签公钥来源；其注册表在装载期校验我的清单/描述符/锁 | **无**（没有模块 `requires: b5-conformance`） | JWKS 不可达 → fail closed（`401`），不降级放行；注册表校验失败 → 宿主启动失败（响亮的，不是我的降级） |
| B3 | 能力 `b3-questions`（报告需要它的路由在组合内） | 无 | 缺失 → 装载失败并指明缺失能力（`ModuleLoadError`） |
| B2 | 能力 `b2-submissions`（同上） | 无 | 同上 |
| B1 | 无（它是宿主提供的静态客户端，不在 capability DAG 内） | 无 | `/app` 属宿主平台路由；B1 缺失时 B5 的报告不受影响 |

**我不 import 任何邻居的代码**（ADR-0011）。测试中唯一一次跨模块导入是
`autograder_b4.registry`，用于断言"同名 entry point 在 B4 一侧也失败"；该文件已注明它不在交付路径上。

## 3. 接口契约冻结清单

| 接口 / 声明 | Owner | 当前状态 | 冻结后的改动方式 |
|---|---|---|---|
| `GET /api/v1/platform/conformance`（路径、状态码、响应 schema） | MAIN | 冻结（CHG-0010；当前版本见 [contracts.lock](../../contracts.lock)） | 先 CHG，再改实现 |
| 路由前缀 `/api/v1/platform` | B5（`module.yaml` + descriptor） | 冻结 | 与上面同一次变更；两处必须同时改，否则装载失败 |
| 能力名 `b5-conformance` | B5 | 冻结 | 改名是契约级变更（其它模块若 `requires` 它会断） |
| `requires` 的四项能力名 | 各提供者（B2/B3/B4/MAIN host） | 冻结 | 提供者改名 → B5 必须同步，否则装载失败（这是期望行为） |
| `ConformanceReport.differences[].kind` 的两个取值 | MAIN | 冻结 | 新增 `kind` = 契约变更 + 实现 + 两侧用例 |
| `ModuleDescriptor` / `PlatformModule`（SDK） | MAIN | 冻结 | 走 MAIN 的 SDK 变更；B5 不自行扩展协议 |

## 4. 与运维的约定

| 事项 | 约定 |
|---|---|
| 端口 | B5 **不新增端口**；随宿主端口（本仓库 18000）提供 |
| readiness | B5 的健康项报告"契约树可读性"；不报告数据库/NATS（它不连接它们） |
| 日志 | 只在拒绝请求时产生一条结构化 WARNING；不含令牌、cookie、请求体 |
| 数据 | 无持久化；无备份、无清理、无迁移 |
