# B5 文档索引（唯一入口）

本文件是 B5 文档的唯一索引。**不在下表 / 状态表内的正式文档视为治理缺陷**；新增 / 移动 / 归档文档必须同步更新本文件。

> 相对路径约定：B5 内部用相对路径；引用 MAIN 用 `../../MAIN/...`（从本 `docs/` 层看；从模块根 `B5/` 看是 `../MAIN/...`）。
> 本仓库假设 **B5 与 MAIN 位于同一父目录**（[contracts.lock](../contracts.lock) 的 `source: ../MAIN/contracts` 即此约定）。

## 权威表

| 主题 | 权威位置 | 本模块文档作用 |
|---|---|---|
| 需求编号 / 原文 / 验收 | `../../MAIN/docs/requirements/rq-catalog.md` | 本模块**不认领** `RQ-B-*`（原因见 `requirements/traceability.md`），只写约束来源 |
| B5 存在的理由与必须证明的事 | `../../MAIN/docs/adr/ADR-0013-open-module-count.md`、`../../MAIN/docs/contracts/changes/CHG-0010-b5-conformance-module.md` | 摘要 + 落到实现与用例（`requirements/`、`testing/`） |
| HTTP / JSON 字段 / 状态码 | `../../MAIN/contracts/openapi/b5.yaml`、`../../MAIN/contracts/openapi/common.yaml` | 只写"我提供哪个端点、什么条件下返回哪个状态码"，**禁止**复制字段表 |
| 契约版本与摘要 | [contracts.lock](../contracts.lock) | 引用文件，不写死字面值 |
| 路由 / 事件 / 能力声明 | [module.yaml](../module.yaml) | 摘要，以 `module.yaml` 为准 |
| 运行期结构真源 | `src/autograder_b5/plugin.py` 的 `ModuleDescriptor` | 说明与 `module.yaml` 的一致性由 `checks.check_module_descriptors()` 强制 |
| 跨模块边界与不可逆决策 | `../../MAIN/docs/adr/**` | 引用编号；本模块决策见 `adr/` |
| 阶段完成证据 | `phases/` | 必须写**真实执行过的命令与结果** |
| 安全与日志规则 | `../../MAIN/docs/api/governance.md`、`../../MAIN/docs/api/identifiers.md` | 落地为 `architecture/boundaries.md` 的检查项 |

## 状态表

状态词仅用：`骨架` / `待实现` / `已完成` / `阻塞`。基线：Phase 01（[phase-01-platform-conformance.md](phases/active/phase-01-platform-conformance.md)）。

| 文档 / 能力 | 状态 | 最后验证方式 |
|---|---|---|
| 插件注册（descriptor / entry point / health） | `已完成` | 一致性套件的真实 loader 装载 + `checks.check_module_descriptors()` |
| `GET /api/v1/platform/conformance` 三视图报告 | `已完成` | `tests/test_conformance_endpoint.py`（对组装后的 app 发真实请求，响应体按 `b5.yaml` schema 校验） |
| Bearer 本地验签（EdDSA / JWKS / 注册声明） | `已完成` | 同上（未认证、畸形令牌、错误 `aud`、有效令牌四类） |
| 三视图比对逻辑（两种 `kind`） | `已完成` | `tests/test_conformance_views.py`（含"主机路由不算差异""前缀包含不算差异"两个反例） |
| 即插即用负例（缺能力 / 能力环 / 重复路由 / 描述符类型错 / 同名 entry point） | `已完成` | `tests/test_load_failures.py`（5 例，全部走真实 loader，1 例注明只走 B4 注册表） |
| 治理发现（门禁自动纳入 B5、B5 不拥有 schema） | `已完成` | `tests/test_governance_discovery.py`（调用 MAIN 真实门禁函数） |
| 独立部署（无 MAIN 源码的机器上运行） | `待实现` | 未验证；B5 与 MAIN 同仓库布局，未做跨机部署演练 |
| 跨模块端到端（真端口 + 真实 B4 令牌） | `待实现` | 未验证；见 `docs/testing/strategy.md` §未执行项 |

## 文档导航

- 需求：`requirements/scope.md` · `traceability.md` · `acceptance.md`
- 架构：`architecture/overview.md` · `components.md` · `models.md` · `boundaries.md`
- 接口：`api/contract.md` · `api/events.md`
- 数据库：`database/schema.md` · `migrations.md`
- 测试：`testing/strategy.md` · `cases.md`
- 决策：`adr/README.md`（ADR-0001）
- 运行：`runbooks/local-development.md` · `troubleshooting.md`
- 阶段：`phases/active/phase-01-platform-conformance.md` · `phases/archive/README.md`
- 协作：`collaboration/onboarding.md` · `workpackages.md` · `interfaces.md` · `definition-of-done.md` · `glossary.md`
- 知识：`knowledge/README.md`
