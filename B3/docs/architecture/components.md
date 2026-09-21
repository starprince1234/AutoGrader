# B3 组件职责（Components）

组件均已实现；下表把"职责"映射到**真实文件**与**测试**，便于定位与并行维护。核心/扩展点的划分是本模块最重要的结构约束。

## 组件清单

| 组件 | 职责 | 实现位置 | 关键测试 |
|---|---|---|---|
| 题目聚合（Question Aggregate） | 题干 + 参考答案 + 测试用例三合一，绑定到不可变版本；标签写路径与规范化，以及按标签的**全部满足**检索 | `domain/questions.py`、`domain/content.py`、`domain/tags.py`、`domain/catalog.py`、`infra/repositories.py` | `test_versions.py`、`test_questions_api.py`（含标签交集语义） |
| 版本管理（Version Management） | 不可变版本：完整性校验、发布（含凭证门禁）、归档、删除限制；版本 DTO 带修订理由与结构化差异 | `domain/versions.py`、`domain/version_content.py` + 迁移中的 5 个触发器 | `test_versions.py`（31 例） |
| 参考答案凭证（Reference Attestation） | 记录外部验证器的声明，并在发布时强制它与**服务端重算摘要**一致；B3 自身不执行任何东西 | `domain/attestations.py`、`infra/models.py: ReferenceAttestation` | `test_versions.py`、`test_questions_api.py`（无凭证 422 / 摘要不符 409 / 改内容自动失效） |
| 资源存储（Resource Storage） | 题干图片：类型白名单 + 魔数校验 + 内容摘要 + 受控读取 | `domain/assets.py`、`infra/storage.py`、`api/router.py`（`_asset_headers`） | `test_questions_api.py`（图片 403 / 安全响应头 / SVG 拒绝） |
| 参考答案受保护存储（Protected Storage） | 参考答案 / 隐藏用例 / fixture 字节的独立权限存取 | `infra/models.py`（`reference_answers`/`test_cases`/`blobs`/`fixtures`）、迁移中的 `REVOKE`、`infra/repositories.py: ProtectedContentRepository` | `test_migrations.py`（权限拒绝）、`test_versions.py`（摘要绑定） |
| package 编译器（Package Compiler） | 题目版本 → 不可变 package；绑定 `package_digest` | `compile/compiler.py`、`compile/canonical.py` | `test_adapters.py`（确定性、契约校验） |
| 统一校验入口（Manifest Validation） | 三型 manifest 的唯一判据：路径 / 标识符 / 参数 / 枚举 / 跨集合一致性 | `compile/manifest.py`、`compile/validation.py` | `test_adapters.py`（10 例越界路径、6 例非法入口） |
| 比较策略诊断（Comparison Diagnostics） | 对**已声明**的比较策略做适配性判断（CRLF / 编码 / 尾随空白 / 缺容差）；纯函数，零模型调用。上表是规则，下表一行是它的**读路径**（谁可读、诊断哪个版本、结果如何变成公开形状） | `compile/diagnostics.py`（规则）、`domain/diagnostics.py`（读路径） | `test_adapters.py`（5 例；每条规则指向真实故障）、`test_questions_api.py`（诊断端点 4 例） |
| 三类 adapter 校验（stdin/file/callable） | 各型的专属语义与 package 段构造 | `compile/adapters.py` + `compile/registry.py` | `test_adapters.py` |
| 修订闭环（Revision Loop） | 新版本相对被取代版本的结构化差异（计数 / 摘要 / 存在性，绝不含正文）；键集合**就等于**契约 `RevisionDiff`，落库形状与公开形状是同一个 | `domain/revisions.py`（`snapshot_content` / `compute_revision_diff` / `revision_note`）、`domain/questions.py`（何时计算）、`infra/repositories.py: create_version`（持久化） | `test_versions.py`（键集合断言直接读契约文件；作者手写理由优先；首版不可比较） |
| 事件消费器（Event Consumer） | 订阅去标识化事件、白名单校验、inbox 去重、投递聚合、**逐投递结果计数** | `events/consumer.py`、`events/whitelist.py`、`infra/models.py: ConsumptionCounter` | `test_statistics.py`、`test_events.py` |
| 统计聚合（Stats Aggregator） | 整数预聚合 + 重建 + 有界窗口扫描（keyset 分页） | `stats/aggregator.py`、`stats/service.py`、`stats/buckets.py`、`infra/repositories.py: inbox_rows_in_window` | `test_statistics.py`（39 例） |
| 消费健康（Consumption Health） | 四态区分"没有事件"与"事件被拒收"；并入 `HealthReport` 与 CLI | `stats/health.py`、`api/app.py: health_detail`、`consumer_cli.py --health` | `test_statistics.py`、`test_consumer_cli.py` |
| 发布 outbox（Outbox） | 事件与业务变更同事务写出，投递失败保留重试 | `domain/outbox.py`、`infra/models.py: EventOutbox`、`infra/publisher.py` | `test_events.py` |
| 认证与作用域（Auth） | EdDSA/JWKS 本地验签、角色与课程作用域策略、401/403 语义、服务令牌 | `auth.py`、`api/deps.py` | `test_auth.py`（23 例，真实签名） |
| 结构化日志（Logging） | `producer=B3` 脱敏 JSON 日志 | `logging.py` + `api/app.py` 中间件 | `test_logging.py`（23 例） |
| 公开/内部 DTO 分离层 | 公开模型、内部模型、白名单序列化 | `dto/public.py`、`dto/internal.py`、`dto/wire.py`、`api/serializers.py` | `test_dto_boundary.py`、`test_questions_api.py` |

## 依赖方向与"核心不改、扩展可加"

- **核心（不随题型改动而改）**：`compile/compiler.py`、`compile/manifest.py`、`compile/registry.py`、`api/serializers.py`、迁移中的不可变约束。
- **可扩展（新增题型只加插件式 adapter）**：`compile/adapters.py` 中的 `AdapterCompiler` 实现 + 注册表登记。

### 扩展点：如何新增一种题型而不改 B2 runner 核心

1. 新题型 = 一个 `AdapterCompiler` 实现（`validate` + `to_package_section`），复用 `evaluation-package.schema.json` 的**同一** package 结构与**同一**校验入口；**不新增并列的 package 结构**。
2. 在 `compile/registry.py` 登记（`register_adapter_compiler`）。
3. 为该题型加一个端到端示例题目（`tests/examples.py`），证明它经同一入口编译并通过契约校验。
4. 若新题型的 `kind` 不在契约枚举内，需要 **MAIN 契约变更**（这是契约门槛，不是编译器耦合）；B3 不自行扩展 schema。
5. B2 runner 只按 package 的 `adapter.kind` 分派，**不感知题目业务语义**，因此新增题型不改 B2 核心。

> 违反此扩展点的典型反模式：为三种题型设计三套并行 package 结构。`tests/test_adapters.py::test_all_three_adapters_produce_the_same_package_shape` 与 `::test_compiler_core_dispatches_through_the_registry_only`（AST 断言核心无 kind 分支 + 运行时注册新 adapter）就是防这个的。

## 组件间共享文件（并行开发需协调）

- `plugin.py` 的 `descriptor`；`alembic/` 迁移；`dto/` 两个模型模块；`api/serializers.py`（公开响应唯一出口）；`errors.py` 错误码。修改前先在 [../collaboration/workpackages.md](../collaboration/workpackages.md) 认领并沟通，并按 [../collaboration/frozen-interfaces.md](../collaboration/frozen-interfaces.md) 的冻结规则处理（改 ADR → 改代码 → 同步清单）。
