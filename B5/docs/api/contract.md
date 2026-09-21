# 接口：我提供什么 / 我消费什么

契约权威：`../../../MAIN/contracts/openapi/b5.yaml`（端点）与
`../../../MAIN/contracts/openapi/common.yaml`（共享组件与状态码语义）。
版本与摘要见 [contracts.lock](../../contracts.lock)（**引用文件，不在此复制**）。

## 1. 我提供的端点

| 方法 + 路径 | 用途 | 认证 | 授权 |
|---|---|---|---|
| `GET /api/v1/platform/conformance` | 返回"运行中的组合 vs 契约声明 vs 宿主平台路由"的三视图差异报告 | `Authorization: Bearer <EdDSA JWT>`（B4 签发） | 任何已认证主体 |

路由前缀 `/api/v1/platform` 由 [module.yaml](../../module.yaml) 与运行时 `ModuleDescriptor` 声明
（两者的一致性由 `autograder_host.checks.check_module_descriptors()` 与 B4 的注册表在装载期强制）。

**响应体字段、枚举值、长度限制一律以契约为准**，本文件不复制。字段只有四个概念：
是否一致、装载了哪些模块（按 capability DAG 顺序）、契约路径条数、差异列表（每条含种类与说明）。

## 2. 我消费的端点 / 事件

| 消费对象 | 来源 | 用途 | 频率 |
|---|---|---|---|
| `GET /api/v1/auth/jwks` | B4（契约 `b4.yaml`） | 取验签公钥（仅公钥） | 进程内缓存 300 秒；仅在缓存未命中时请求 |
| `MAIN/contracts/openapi/*.yaml` | MAIN（文件，非 HTTP） | 视图 1 | 每个请求重读一次（文件小；不缓存以保证报告反映**当前**契约） |
| 宿主进程的 `app.state.modules` | MAIN host（进程内） | 视图 3 | 每个请求 |

**我不消费任何事件**（见 [events.md](events.md)）。

## 3. 错误约定

遵循 `../../../MAIN/docs/api/governance.md`：错误体是 RFC 9457 Problem Details，
`Content-Type: application/problem+json`，时间为 UTC RFC 3339，标识符为 UUID。

本端点能返回的状态码**只有契约里声明的那些**：

| 状态码 | 何时 | 备注 |
|---|---|---|
| `200` | 验签通过 | 报告是否 `consistent` 与状态码无关——**不一致正是调用者来读的东西** |
| `401` | 缺少/畸形/无法验签的令牌；或验签公钥不可获取 | 不区分"令牌不存在"与"主体不存在"；`detail` 面向运维但可公开 |

**没有 `403` / `404`**：本端点没有角色或作用域判定，也没有资源标识符，因此这两个语义不可达。
添加它们会声明一个模块永远不返回的状态码。

`500` 不在契约中，但当一个**部署**故障使报告无法构造时（`CONTRACTS_DIR` 指向的目录里没有
OpenAPI 文档），B5 返回 Problem Details 形状的 `500` 而不是伪造一份空报告——
伪造报告会让"一致"变成假消息。

## 4. 请求头

| 头 | 处理方式 |
|---|---|
| `Authorization` | 必需。`Bearer <EdDSA JWT>`；本地验签（`auth.py`） |
| `traceparent` | 契约声明的可选头。B5 **不向下游传播**（本模块没有出站调用），只在拒绝请求的日志行中原样记录，用于与其它模块的日志串联 |
| 其它头 | 忽略 |

## 5. 已知缺口

1. **不按方法比对**：报告比对"契约路径 vs 模块路由前缀"，不比对 HTTP 方法。
   `module.yaml` 与 descriptor 两侧都只声明路径，方法级比对缺少第二个权威来源。
2. **不校验响应体是否真的幂等/无副作用**：只读是靠代码审查与
   [../requirements/acceptance.md](../requirements/acceptance.md) AC-13 的检索动作保证的，
   没有运行期强制——一个只读模块没有可观测的写行为可供断言。
