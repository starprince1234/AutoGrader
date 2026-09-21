# B5 验收标准

本文件的每一条都能被**执行**：前置条件是命令，期望结果是断言，失败判定是"命令退出码非零或断言不成立"。
措辞中的"已验证"只用于本仓库真实跑过的命令（记录见
[../phases/active/phase-01-platform-conformance.md](../phases/active/phase-01-platform-conformance.md)）。

## 1. 可插拔性（ADR-0013 / PRD §8.6）

### AC-01 不改 MAIN 代码即可装载

- **Given** 一个干净环境：`cd B5 && uv sync`
- **When** 执行
  ```python
  import sys; sys.path.insert(0, "MAIN/host/src")
  from autograder_host.main import _load_modules
  print([m.descriptor.module for m in _load_modules()])
  ```
- **Then** 输出包含 `B5`，且顺序满足 capability DAG。
- **失败判定**：异常、或结果中没有 `B5`、或某个模块排在其 `requires` 的提供者之前。
- **对照**：本次改动对 MAIN 的修改只有 `MAIN/pyproject.toml` 的两行依赖声明（CHG-0010 规定），
  host 代码、契约文件、其他模块共 0 字节改动。

### AC-02 顺序由 `requires` 决定，而不是巧合

- **Given** `AUTOGRADER_MODULES=_probe_modules:depends_on_b5_conformance`（一个名为 `B0`、
  却 `requires: b5-conformance` 的探针模块）
- **When** 再次调用 `_load_modules()`
- **Then** `B0` 排在 `B5` **之后**——尽管按名字排序它在最前。
- **失败判定**：`B0` 出现在 `B5` 之前（说明排序读的不是 capability）。
- **覆盖测试**：`tests/test_composition_load.py::test_b5_is_placed_by_capability_rather_than_by_name`

### AC-03 治理门禁自动纳入 B5

- **When** `cd ../MAIN && .\.venv\Scripts\python.exe -m autograder_host.checks`
- **Then** 三条 "ok" 输出齐全，B5 被文档治理、编码/链接、迁移 head 三项检查覆盖（无需登记）。
- **失败判定**：任何一条输出缺失或抛错。
- **覆盖测试**：`tests/test_governance_discovery.py`（逐项调用同一批门禁函数）

## 2. 端点行为（`../MAIN/contracts/openapi/b5.yaml`）

### AC-04 未认证一律 401，且是 Problem Details

- **Given** 组装后的应用（`autograder_host.main.create_app()`）+ `TestClient`
- **When** `GET /api/v1/platform/conformance`，无 `Authorization` 头
- **Then** `401`，`Content-Type: application/problem+json`，响应体符合 `common.yaml` 的 `ProblemDetails`，
  `instance` 等于请求路径。
- **失败判定**：其他状态码、非 problem+json、字段缺失、或响应里出现"该用户是否存在"的信息。
- **覆盖测试**：`tests/test_conformance_endpoint.py::test_an_unauthenticated_caller_is_refused_with_problem_details`

### AC-05 畸形/过期/错误受众的令牌同样 401

- **When** 依次发送：非 JWT 文本、由其他密钥签名但格式正确的 JWT、`aud` 不匹配的 JWT
- **Then** 三次都 `401`，且 `detail` 不区分"令牌不存在"与"主体不存在"。
- **覆盖测试**：`test_a_malformed_credential_is_refused_the_same_way`、
  `test_a_token_for_another_audience_is_refused`

### AC-06 有效令牌读到报告，且报告符合契约 schema

- **Given** 一枚由本地 Ed25519 私钥签发、公钥已发布到 JWKS 的令牌
- **When** `GET /api/v1/platform/conformance` 带上它
- **Then** `200`，响应体通过 `b5.yaml` 的 `ConformanceReport` schema 校验。
- **失败判定**：schema 校验失败（`additionalProperties` / 枚举 / `maxLength` 任一不合）。
- **覆盖测试**：`test_an_authenticated_caller_reads_a_consistent_report`

### AC-07 报告能指出不一致（不是只会通过）

- **When** 用构造的契约/模块视图调用 `conformance.build_report()`
- **Then** 契约路径无人服务 → `contract_path_unserved`，detail 同时给出**契约文件名**与**路径**；
  模块声明了契约未描述的前缀 → `descriptor_route_undeclared`，detail 同时给出**模块名**与前缀。
- **并且** 两个反例不得被误报：宿主的 `/health/*` 路由不算差异；契约全路径落在模块前缀之下不算差异。
- **覆盖测试**：`tests/test_conformance_views.py`（5 例）

## 3. 违反规范必须响亮失败（CHG-0010 §5.4）

| 编号 | 场景 | 期望的**具体**失败 | 失败判定 |
|---|---|---|---|
| AC-08 | 某个模块 `requires` 一个没人提供的能力 | `ModuleLoadError`，消息中出现该能力名与模块名 | 静默装载、或报错但不指明能力名 |
| AC-09 | 两个模块互相 `requires`（能力环） | `ModuleLoadError`，消息中出现两个模块名与两条能力名 | 任意打破环、或挂起 |
| AC-10 | 两个模块声明同一路由前缀 | `ModuleLoadError`，消息中出现 `duplicate module routes` 与冲突前缀 | 静默覆盖 |
| AC-11 | 可装载对象的 `descriptor` 不是 `ModuleDescriptor` | `ModuleLoadError`，消息为 `did not provide a ModuleDescriptor` | 继续装载 |
| AC-12 | 两个 entry point 同名（ADR-0013 决策 5） | host：`ModuleLoadError`；B4 注册表：`RegistryError`——**两处都失败** | 只有一处失败（历史上两处口径相反，正是缺陷本身） |

覆盖测试：`tests/test_load_failures.py`（5 个用例，其中 AC-12 的注册表一侧直接调用
`autograder_b4.registry.resolve_declared_modules()`，**没有**经过 host loader——见该文件 docstring）。

## 4. 只读与边界

### AC-13 端点无写副作用

- **When** 通读 `src/autograder_b5/{api,conformance,contracts,auth}.py`
- **Then** 只有文件读取、`app.state` 读取与网络读取（JWKS）；没有数据库、没有事件发布、
  没有文件写入、没有迁移。
- **失败判定**：出现任何写入 API（`open(...,"w")`、SQL、publish）。

### AC-14 不碰别的模块的数据

- **Then** `module.yaml: database.schema: none`；`module_schema_owners()` 不含 B5；
  `src/autograder_b5/**` 中不存在 `import autograder_b2|b3|b4|host`（ADR-0011）。
- **失败判定**：`checks.module_schema_owners()` 含 B5，或源码出现跨模块 import。
- **覆盖测试**：`tests/test_governance_discovery.py::test_b5_is_not_a_database_schema_owner`

## 5. 未执行的验收（如实列出）

| 项 | 为什么未执行 |
|---|---|
| 真实端口上的 e2e（uvicorn + 真 B4 令牌） | 会与已在运行的服务争用端口；改用 `TestClient` 打组装后的 app |
| 跨机独立部署（只有 B5 源码的机器） | 未做部署演练。`CONTRACTS_DIR` 默认值经 SDK 的 `deployment_root()` 解析（`B4_MODULE_ROOT` + 校验到真实契约树），因此已安装形态不依赖仓库布局；跨机仍需演练 |
| 生产规模下的报告时延 | 契约路径 37 条，规模不构成风险；未做基准 |
