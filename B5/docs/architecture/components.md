# 组件与扩展点

## 1. 组件清单

| 组件 | 文件 | 职责 | 输入 | 输出 | 依赖 |
|---|---|---|---|---|---|
| 插件边界 | `src/autograder_b5/plugin.py` | 声明 `ModuleDescriptor`；由 `router(context)` 安装设置并交出 router；`health(context)` 报告契约树可读性；`configure_app` 装错误处理器 | host 的 `ModuleContext` | `APIRouter` / `HealthReport` | config、api、contracts、errors |
| HTTP 边界 | `src/autograder_b5/api.py` | 唯一路由 `GET /api/v1/platform/conformance`；调用验签；把 `B5Error` 翻成 RFC 9457 | `Request`、`Authorization` 头 | `ConformanceReport` / `JSONResponse` | auth、conformance、config、errors |
| 比对核心 | `src/autograder_b5/conformance.py` | 三视图比对；定义 `ConformanceReport` / `Difference` 模型与两种 `kind`；`under()` 前缀包含判定 | 契约目录、descriptor 序列、应用路由路径 | `ConformanceReport` | contracts、SDK |
| 契约读取 | `src/autograder_b5/contracts.py` | 读 `MAIN/contracts/openapi/*.yaml`，返回去重排序的"路径 + 所属文件" | 契约目录 | `tuple[ContractPath, ...]` | PyYAML、errors |
| 验签 | `src/autograder_b5/auth.py` | Bearer 提取、JWKS 缓存（按 URL、300s）、`kid` 选键、EdDSA 验签、注册声明校验 | `Authorization` 头、`Settings` | 通过或 `Unauthenticated` | PyJWT、errors |
| 配置 | `src/autograder_b5/config.py` | `Settings`（4 个变量，全部有本地默认值）；进程级安装/读取 | 环境映射 | `Settings` | — |
| 错误面 | `src/autograder_b5/errors.py` | `B5Error` → ProblemDetails；`401` 与"契约树不可读"两类 | detail 文本 | problem 字典 | — |

## 2. 扩展点：新增一种差异 `kind`

`ConformanceReport.differences[].kind` 是契约里的枚举，**新增一种差异是契约变更**，顺序固定：

1. 走 [CHG 流程](../../../MAIN/docs/contracts/change-protocol.md) 修改 `MAIN/contracts/openapi/b5.yaml` 并升版本；
2. 更新 `conformance.DifferenceKind`（`Literal`）与 `build_report()` 中的一个判定块；
3. `tests/test_conformance_views.py` 增加一例"该差异会出现"与一例"不该出现时不出现"；
4. 同步 `docs/api/contract.md` 与 `docs/testing/cases.md`。

**核心不改、扩展可加**的边界在这里是明确的：`build_report()` 的骨架（三视图 → 逐条差异）不因新增
`kind` 而改动，改动只落在"多一个判定块 + 多一个枚举值"上。不需要新增差异种类时不要预留钩子——
本模块没有插件化的比对规则注册表。

## 3. 扩展点：新增一个视图来源

三个视图各自的来源都被固定为**单一权威**：契约文件、宿主路由表、SDK 描述符。把任何一个换成
"从别处推断"都会让比对失去意义（例如用运行中应用的 OpenAPI 反推契约，就等于拿自己跟自己比）。
若将来确需第四个视图，应先有 ADR 说明"为什么现有三方不足以回答这个问题"。

## 4. 明确不做扩展的地方

| 位置 | 为什么保持封闭 |
|---|---|
| 比对规则注册表 | 只有两种差异，且都来自契约枚举；注册表是为不存在的第三种差异做抽象 |
| 认证策略 | 契约规定"任何已认证主体可读"，没有角色/作用域分支；加策略会引入它并不执行的授权判断 |
| 缓存后端 | JWKS 缓存是进程内 300s；换 Redis 只会让一个只读模块多一个依赖 |
| 报告持久化 | 报告按需生成；存下来就会变成第二份事实源（正是本模块要消灭的东西） |
