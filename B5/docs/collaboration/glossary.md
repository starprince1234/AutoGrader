# B5 术语

只列本模块专有或在本模块有特定含义的术语。与 MAIN 共用的术语（契约、契约摘要、capability、
Problem Details、schema 所有权）以 `../../../MAIN/docs/glossary.md` 为准，本文件不重新定义。

| 术语 | 含义 | 在本模块的落点 |
|---|---|---|
| **视图（view）** | 平台接口面的一种权威描述 | 视图 1 = 契约文件；视图 2 = 宿主平台路由；视图 3 = 已装载模块的 descriptor |
| **宿主平台路由** | 组合宿主自己服务、且**没有任何已装载模块声明覆盖**的路由 | `/health/live`、`/health/ready`、`/app` 挂载、FastAPI 自带的 `/docs` 等。它们**有意**在契约之外，因此不算差异 |
| **契约路径** | `MAIN/contracts/openapi/*.yaml` 的 `paths` 下的完整路径 | 例如 `/api/v1/auth/session`；视图 1 的元素 |
| **路由前缀** | `module.yaml` / descriptor 里声明的前缀 | 例如 `/api/v1/auth`；视图 3 的元素。与契约路径的关系是**包含**而不是相等 |
| **差异（difference）** | 三视图之间的一处不一致 | 只有两种：`contract_path_unserved`、`descriptor_route_undeclared`（取值由契约定义，本文件不复制 schema） |
| **缺失侧** | 差异中"谁没有它" | 每条差异的 `detail` 必须同时给出**来源**（契约文件或模块）与**对象**（路径或前缀）；只给计数无法据以行动 |
| **一致（consistent）** | 差异列表为空 | 表示"契约、宿主路由与已装载模块三者不矛盾"，**不表示**平台业务正确 |
| **可插拔（plug and play）** | 遵守规范的模块无需修改 MAIN 代码即可被装载并被治理 | 本模块是它的验收物；证据在 `tests/test_composition_load.py` 与 `test_governance_discovery.py` |
| **能力（capability）** | 模块在 descriptor/清单里 `provides` 的自由字符串 | B5 提供 `b5-conformance`；`requires` 使用既有模块的真实能力名 |
| **fail closed** | 无法完成认证判定时拒绝请求 | 密钥集不可达/无可用键 → `401`，绝不返回报告 |
| **拒绝收集（refuse to collect）** | 缺集成环境时套件在导入期报错，而不是 skip | 见 [../testing/strategy.md](../testing/strategy.md) §5 |
