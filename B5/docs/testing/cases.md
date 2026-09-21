# 测试用例目录

这张表是"能不能证明需求被满足"的检查单。用例 ID 与
`tests/**` 中的函数一一对应；`存在` 列的取值只有"存在"与"待建"。

## 1. 装载与组合（`tests/test_composition_load.py`）

| 用例 | 目标约束 | 层次 | 前置 | 步骤要点 | 期望结果 | 存在 |
|---|---|---|---|---|---|---|
| TC-B5-01 | ADR-0013（新模块靠 entry point 被发现） | 集成 | `uv sync` | 读 `autograder.modules` entry point 表 | `b5 → autograder_b5.plugin:create_module` | 存在 |
| TC-B5-02 | CHG-0010 §5.3 | 集成 | 同上 | 调用真实 `_load_modules()` | 顺序 `["B4","B3","B2","B5"]` | 存在 |
| TC-B5-03 | ADR-0013（capability DAG 真实生效） | 集成 | 同上 | 逐个模块检查 `requires ⊆ 已提供` | 无模块早于其依赖装载 | 存在 |
| TC-B5-04 | CHG-0010 §5.3（顺序不是巧合） | 集成 | 探针模块 | 声明 `B0`（`requires: b5-conformance`） | `B5` 在 `B0` 之前 | 存在 |
| TC-B5-05 | CHG-0010 §3（B5 必须声明真实依赖） | 单元 | 同上 | 读 B5 descriptor | `requires` 恰为 host/b4-auth/b3-questions/b2-submissions；`provides` 恰为 `b5-conformance` | 存在 |

## 2. 违规组合必须失败（`tests/test_load_failures.py`）

| 用例 | 目标约束 | 层次 | 前置 | 步骤要点 | 期望结果 | 存在 |
|---|---|---|---|---|---|---|
| TC-B5-10 | AC-08 | 集成（真实 loader） | 探针 `require_unprovided_capability` | `AUTOGRADER_MODULES` 追加该探针 | `ModuleLoadError`，消息含 `b98-nothing-provides` 与 `B99` | 存在 |
| TC-B5-11 | AC-09 | 集成（真实 loader） | 两个互指的探针 | 同时声明两个探针 | `ModuleLoadError`，消息含两个模块名与两条能力名 | 存在 |
| TC-B5-12 | AC-10 | 集成（真实 loader） | 探针声明 `/api/v1/auth` | 与真实 B4 冲突 | `ModuleLoadError`，含 `duplicate module routes` 与该前缀 | 存在 |
| TC-B5-13 | AC-11 | 集成（真实 loader） | 探针的 `descriptor` 是 dict | 声明该探针 | `ModuleLoadError`：`did not provide a ModuleDescriptor` | 存在 |
| TC-B5-14 | AC-12（host 一侧） | 集成（真实 loader） | 两次声明 B4 的工厂 | 同名 entry point | `ModuleLoadError`：`duplicate module entry point name` | 存在 |
| TC-B5-15 | AC-12（B4 一侧） | 集成（**非 loader**：直接调 `autograder_b4.registry.resolve_declared_modules`） | 同上 | 同上 | `RegistryError`：`declared twice` | 存在 |

> TC-B5-15 是本套件中唯一**没有**经过 host loader 的负例。它的目的正是断言"另一处也失败"，
> 因此必须调用另一处；这一点在测试文件的 docstring 里同样写明。

## 3. 端点行为（`tests/test_conformance_endpoint.py`）

| 用例 | 目标约束 | 层次 | 前置 | 步骤要点 | 期望结果 | 存在 |
|---|---|---|---|---|---|---|
| TC-B5-20 | AC-04 | 接口 | 组装后的 app + `TestClient` | 无 `Authorization` 头 | `401` + `application/problem+json` + 符合 `common.yaml` 的 `ProblemDetails` | 存在 |
| TC-B5-21 | AC-05 | 接口 | 同上 | `Bearer not-a-token` | `401` + ProblemDetails | 存在 |
| TC-B5-22 | AC-05 | 接口 | 同上 | 验签正确但 `aud` 不符的令牌 | `401` + ProblemDetails | 存在 |
| TC-B5-23 | AC-06 | 接口 | 夹具 issuer（Ed25519 + JWKS） | 有效令牌 | `200`，响应体通过 `b5.yaml` 的 `ConformanceReport` schema；`consistent: true`；`loaded_modules == ["B4","B3","B2","B5"]` | 存在 |
| TC-B5-24 | AC-06（自审） | 接口 | 同上 | 核对报告是否覆盖 `b5.yaml` 自己的路径 | `contract_path_count` 等于契约路径总数，且该路径来自 `b5.yaml` | 存在 |

## 4. 比对逻辑（`tests/test_conformance_views.py`）

| 用例 | 目标约束 | 层次 | 前置 | 步骤要点 | 期望结果 | 存在 |
|---|---|---|---|---|---|---|
| TC-B5-30 | AC-07 | 单元 | 临时契约目录（`tmp_path`） | 契约声明一条无人服务的路径 | 恰两条差异（各一种 `kind`），detail 含文件/路径/模块名 | 存在 |
| TC-B5-31 | AC-07 | 单元 | 同上 | 模块声明契约未描述的前缀 | 只有 `descriptor_route_undeclared`，detail 含该前缀 | 存在 |
| TC-B5-32 | AC-07（反例） | 单元 | 同上 | 契约路径由**宿主**服务 | 无差异；换成宿主不服务时恰好一条 `contract_path_unserved` | 存在 |
| TC-B5-33 | AC-07（反例） | 单元 | 同上 | 契约全路径落在模块前缀下 | 无差异（字面比较会误报） | 存在 |
| TC-B5-34 | 契约语义 | 单元 | 同上 | 两个契约文件、一个粗前缀 | `contract_path_count` 按**契约**计数而不是按声明计数 | 存在 |

## 5. 治理（`tests/test_governance_discovery.py`）

| 用例 | 目标约束 | 层次 | 前置 | 步骤要点 | 期望结果 | 存在 |
|---|---|---|---|---|---|---|
| TC-B5-40 | CHG-0010 §5.1 | 治理 | MAIN 源码可导入 | `checks.discovered_modules()` | `["B1","B2","B3","B4","B5"]` | 存在 |
| TC-B5-41 | CHG-0010 §5.2 | 治理 | 同上 | `checks.module_schema_owners()` | `["B2","B3","B4"]`（不含 B5） | 存在 |
| TC-B5-42 | 规范 §7 | 治理 | 同上 | `checks.check_module_descriptors()` | 通过（B5 的 descriptor 与 `module.yaml` 一致） | 存在 |
| TC-B5-43 | 规范 §2 | 治理 | 同上 | `checks.check_module_governance()` | 通过（文档集合完整） | 存在 |
| TC-B5-44 | 契约与路由一致 | 治理 | 同上 | `checks.check_route_declarations()` | 通过 | 存在 |
| TC-B5-45 | 摘要与版本 | 治理 | 同上 | `checks.check_manifests()` | 通过（B5 的锁等于实时契约摘要） | 存在 |

## 6. 待建用例

| 用例 | 目标 | 为什么还没建 |
|---|---|---|
| TC-B5-90 | 真实端口 e2e：uvicorn + 真实 B4 登录令牌 | 需要占用端口与 B4 私钥；见 [strategy.md](strategy.md) §6 |
| TC-B5-91 | 跨机部署（只有 B5 与 MAIN 的目录布局） | 未做部署演练 |
| TC-B5-92 | 契约变更后门禁变红（摘要漂移） | 需要故意篡改 `MAIN/contracts`，属于破坏性实验；MAIN 侧的 `relock_contracts.py --check` 已覆盖同一风险 |
