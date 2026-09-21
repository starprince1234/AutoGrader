# Phase 00 初始化与文档治理

- **状态**：**active（未归档）**
- **范围**：MAIN 的契约权威、composition host、Platform SDK、完整文档体系，以及 B1/B2/B3/B4 四个独立项目的骨架与文档基线
- **归档判据**：见 `../testing/acceptance.md` §1 与 §2.4；**当前未满足**（条件 9/10/11 未达成）

---

## 1. 目标与范围

### 本阶段交付

1. 五个**独立 Git 项目**（`MAIN`、`B1`、`B2`、`B3`、`B4`），各自具备 `AGENTS.md`、README、`.gitignore`、`.env.example`、`module.yaml`、`contracts.lock`。
2. **唯一契约权威**：`MAIN/contracts/` 下的 OpenAPI 3.1、AsyncAPI 3.0、JSON Schema 2020-12 与示例。
3. **Platform SDK** 与**单个 composition host**（FastAPI）：按 capability DAG 装载插件、拒绝冲突、聚合健康、同源提供 B1 静态产物。
4. **完整文档体系**：需求目录（32 条 RQ 与逐条验收）、架构（上下文/容器/组件/模型/安全/资源）、契约治理与变更规程、数据所有权、测试策略与用例目录、ADR、运行手册、阶段与知识、协作规范与模块文档规范。
5. **自动化门禁**：契约/示例/manifest/摘要/需求基线/模块文档集合/文档编码与链接。
6. **B3 对齐当前需求**：移除旧评分/证据/报告设计的全部痕迹。

### 明确不做（本阶段非目标）

- 不注册任何未实现的业务路由，不返回假数据或 `501` 占位响应。
- 不实现题库、提交、评测、用户等业务能力（属功能阶段）。
- 不创建远端、CI、PR 流程。
- 不做 Linux 安全与资源验收（属功能阶段，见 `../../runbooks/linux-acceptance.md`）。

---

## 2. 输入与决策

| 输入 | 用途 |
|---|---|
| `MAIN/课程项目需求列表.xlsx`（工作表 `AutoGrader作业代码自动评分系统`） | **规范基线**：32 条 `RQ-B-*` |
| `MAIN/课程开发项目-思维导图.pdf` | 产品形态与技术方向参考。**与 XLSX 冲突处以 XLSX 为准** |
| `MAIN/项目初始化与项目文档管理.md` | 文档治理规范来源 |
| B3 目录中的旧材料（`taskone/`、`tools/reporting/`） | 仅作过程材料，**非权威**，且不得从中恢复旧 API |

### 本阶段作出的关键决策

| ADR | 决策 | 影响 |
|---|---|---|
| [ADR-0001](../../adr/ADR-0001-plugin-composition-and-schema-ownership.md) | 微内核 host + 独立打包插件 + 单实例多 schema | 全体 |
| [ADR-0002](../../adr/ADR-0002-current-xlsx-requirements-baseline.md) | XLSX 为规范基线；**导图把安全性与评分结果划给 B3，与 XLSX 冲突，以 XLSX 为准** | 全体（尤其 B2/B3） |
| [ADR-0003](../../adr/ADR-0003-api-authority-and-runtime-registry.md) | 接口权威双层：设计期 MAIN / 运行期 B4 | MAIN、B4 |
| [ADR-0004](../../adr/ADR-0004-sandbox-technology-selection.md) | 沙箱选 nsjail（导图提 Firejail/Docker，属待确认项） | B2 |
| [ADR-0005](../../adr/ADR-0005-deterministic-replayable-scoring.md) | 确定性可重放评分；**LLM 不参与成绩与反馈** | B2、B3 |
| [ADR-0006](../../adr/ADR-0006-event-driven-decoupling.md) | 事件驱动：at-least-once + outbox/inbox + DLQ | 全体 |
| [ADR-0007](../../adr/ADR-0007-b1-static-client-provided-by-host.md) | B1 为静态客户端，由 host 同源提供 | B1、MAIN |

---

## 3. 完成条件

| # | 条件 | 达成 |
|---|---|---|
| 1 | 五个项目根结构、Git、`.gitignore`、`.env.example` 齐备 | ✅ |
| 2 | 各模块 `AGENTS.md`、README、docs 索引、`module.yaml`、`contracts.lock` 齐备 | ✅ |
| 3 | 完整文档体系（需求/架构/API/数据库/测试/ADR/运行手册/阶段/协作/知识） | ✅ |
| 4 | 需求目录固化 32 条 RQ 及逐条验收标准 | ✅ |
| 5 | 契约权威完整，示例通过归属 schema 的实例校验 | ✅ |
| 6 | Platform SDK 协议与 composition host 存在且逻辑完整 | ✅ **Phase 01 已真实装载**：`/health/ready` 聚合 B2/B3/B4；期间修复 `module_settings` 覆盖语义与 `configure_app` 钩子，见 [Phase 01](phase-01-integration-runtime.md) |
| 7 | 契约、需求基线、模块文档、文档链接门禁自动化 | ✅ |
| 8 | B3 旧评分设计完整移除（含磁盘残留产物） | ✅ |
| 9 | 各模块静态检查与测试真实通过 | ✅ **已在 [Phase 01](phase-01-integration-runtime.md) 达成**：B1 `18 files / 96 tests`、B2 `1134 passed`、B3 `282 passed`、B4 `209 passed`（各自独立实例），四条命令均 exit 0 |
| 10 | PostgreSQL/NATS/host 统一启动与 readiness 验证 | ✅ **已在 [Phase 01](phase-01-integration-runtime.md) 达成**：`/health/ready` → `ready=true`，B2/B3/B4 三项依赖逐条点名健康 |
| 11 | 资源实测 | ❌ **未执行**（需 Linux + cgroup v2 + nsjail） |

→ **条件 11 未满足，阶段保持 active。** 条件 6（host 装载）在 Phase 01 已随真实装载转为达成。

---

## 4. 实际变更

### 4.1 五个项目

| 项目 | 交付形态 | 当前能力 |
|---|---|---|
| **MAIN** | 契约 + SDK + host + 文档基座 | 契约门禁可运行；host 装载逻辑存在但未实测 |
| **B1** | React 19 + TS 5.7 + Vite 7 静态客户端 | 15 行占位 App；`typecheck`/`build` 已接入；**无 ESLint / 无测试框架** |
| **B2** | Python 插件（评测） | 21 行插件：descriptor + 空路由 + health |
| **B3** | Python 插件（题库） | 21 行插件；旧设计已移除 |
| **B4** | Python 插件（平台） | 20 行插件 |

### 4.2 契约内容

- `openapi/common.yaml`：通用约定与公共错误响应。
- `openapi/b2.yaml`：提交、评测运行与 SSE、报告、成绩查询与导出。
- `openapi/b3.yaml`：题目、不可变版本与发布/归档、内部 evaluation package、assignment、统计。
- `openapi/b4.yaml`：认证与 JWKS、用户、课程与课序、选课编辑、名单导入（上传/预览/映射/确认）、审计、运行期注册表。
- `asyncapi/events.yaml`：7 个 channel + send/receive operation + 示例。
- `schemas/`：语言枚举、提交信封、评测包、评测报告、名单导入件、事件信封、模块清单。
- `examples/`：5 份示例，全部通过实例校验。
- `CHANGELOG.md`：记录本阶段的**基线收敛**（把语义空洞的占位契约补成可据以并行开发的结构）。

### 4.3 自动化门禁（`host/src/autograder_host/checks.py`）

| 函数 | 作用 |
|---|---|
| `check_contract_examples` | 示例为合法 JSON **且通过归属 schema**；声明的示例必须存在 |
| （schema 循环） | `contracts/schemas/*.json` 均为合法 2020-12 schema |
| `check_manifests` | 清单合法；路由/schema 归属唯一；四份锁与实时摘要一致 |
| `check_requirement_baseline` | 32 条编号完整（**含 `RQ-B-4-99`**）；废弃 B3 标识未在实现面复现 |
| `check_module_governance` | 四个模块的必需文档集合与阶段记录齐备 |
| `check_document_encoding_and_links` | UTF-8、无 BOM、无死链 |

### 4.4 本阶段发现并修复的缺陷

| ID | 缺陷 | 处置 |
|---|---|---|
| R-01 | **B3 已删除的旧评分实现的字节码仍留在磁盘**（`api`/`models`/`schemas`/`services`/`repositories`/`config`/`db`/`main` 的 `.pyc`，含 `EvaluationRun`、`evaluation_runs`、`evidence-scoring` 等；另有已删除的旧迁移与测试的 `.pyc`） | 删除 19 个孤儿字节码文件；把**实现面**纳入自动化检查 |
| R-02 | `RQ-B-4-99` 在 `traceability.md` 与 `checks.py` 中缺失 | 两处补齐，纳入 32 条基线断言 |
| R-03 | 废弃标识检查只扫 `*.md`，既漏掉磁盘残留，又会误伤合规的"记录已移除"表述 | 重写：实现面全字符串扫描；追踪矩阵只拦截"需求声明"式出现 |
| R-04 | 契约示例只校验 JSON 合法性，未做实例校验；且 4 个 schema 语义空洞（`type: object` 无约束） | 扩写 schema；新增实例校验门禁与 5 份示例 |
| R-05 | `infra/postgres/init.sql` 只建 schema，无角色供给 | 记录为缺口，转 B4 功能阶段 |
| R-06 | **`module.yaml` 的 `migration_head` 与实际 Alembic revision 不一致**：B2/B3/B4 声明 `baseline`，而真实 revision id 是 `20260915_0001`。修复时还发现 YAML 会把裸写的 `20260915_0001` 解析为**整数**（下划线是数字分隔符），因此必须加引号 | 三处改为带引号的 `"20260915_0001"`；新增门禁 `check_local_configuration_consistency()` 自动比对清单与迁移链的 head |
| R-07 | **模块本地端口与 compose 不一致**：各模块 `.env.example` 与 `alembic.ini` 指向 `localhost:5432` / `nats://localhost:4222`（容器端口），而 `compose.yaml` 发布的宿主端口是 `55432` / `54222`。按模块文档操作会连接失败 | **高（会直接浪费开发时间）**：六个文件统一为宿主端口；同一门禁锁死该一致性 |
| R-08 | **B3 的 `dev` 组缺 `pytest-cov`**（B2/B4 有）；且 `B3/uv.lock` 中也没有该包 | 当时在无 `uv` 可用的环境手改 `pyproject.toml` 会使 `uv.lock` 失配，因此**未改**，并记录了正确修复方式。**已在 [Phase 01](phase-01-integration-runtime.md) 关闭**：`uv` 可用后按 `uv add --dev` 的等价结果补入 `pyproject.toml` 与 `uv.lock`，实测 `uv run pytest --cov=autograder_b3` → `TOTAL 3206 324 90%`、`282 passed`，`pytest-cov 6.3.0` / `coverage 7.16.1` 均已安装 |
| R-09 | **B2 阶段记录存在 7 条死链**（相对路径层级错误） | 门禁精确报出文件与目标后逐条修正。这类错误人工审查极难发现 |
| R-01b | **R-01 的首次修复不完整，且被当成已完成**：初版 `check_deprecated_b3_artifacts()` 的循环带 `continue` **跳过了 `__pycache__` 与 `.pyc`**——恰好是事故真正发生的面。字节码是手工删的，"下次会被拦住"当时不成立，`knowledge/` 那条笔记的预防措施声明因此**是假的** | **高**：`.pyc` 不再跳过，改为字节读取 + ASCII 匹配（判据＝"被移除的设计是否仍在任何产物面"，无害孤儿不报错）；已按"植入 → 失败 → 清除 → 通过"**实测证明**；知识笔记已更正并标注该次修复不完整 |
| R-10 | **文档内部自相矛盾（会阻塞开工）**：`B1/docs/collaboration/interfaces.md` 的冻结清单只改了一半——第 43–47 行已更正为"已冻结"，第 48–52 行仍残留"成绩查询/导出未冻结""SSE 事件类型表未冻结"，末行甚至写着"标未冻结的项不得开工"，直接阻止 B1-B/B1-C | **高**：删除陈旧行，改为单一准确表格，并写明与 `../api/contract.md` 不一致时须**同时修正两处** |
| R-11 | `B4/docs/api/contract.md` 仍写 OpenAPI"仅声明两个 operation"，与已补全的 `b4.yaml` 及**同一文件下方**的表格矛盾 | 更正为完整 operation 清单，并明确区分"契约已锁定 ≠ 实现已完成" |
| R-12 | **表述超出证据**：曾以"前置工作已全部完成并通过校验"概括本阶段。实际通过校验的只有**契约与文档治理**；host 装载、迁移、Compose、Linux 沙箱、资源实测**均未执行** | 统一改为准确表述：**"契约与文档骨架完成，运行验证待执行"**（`../testing/acceptance.md` §2.1） |

---

## 5. 验证记录

### 5.1 已执行（真实命令与结果）

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

**实际输出**

```text
contracts, manifests and requirements: ok (de8f683496da78438a5cd1e6a12fc2aa30b871c63573ffb49e5e0abe5cc4b617)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok
```

**环境**：Windows；Python 3.12.3（仓库内 `MAIN/.venv`）；任务日期同本阶段。

**独立复核**：直接重算摘要并与四份 `contracts.lock` 比对，四者均相等（`MATCH`）。

**过程记录**：门禁在本阶段被**实际触发并修复**过多次，说明它们不是装饰：
1. 新增实例校验后，`evaluation-package.json` 因摘要长度不足 64 位而失败 → 修正示例；
2. 契约变更后四份锁失配 → 按变更规程重算并更新四份锁（发生两次）；
3. `RQ-B-3-70` 出现在 B3 追踪矩阵的**叙述**中触发误报 → 收窄检查为"仅拦截需求声明"；
4. `module.yaml` 的 `migration_head` 被 YAML 解析为整数 → 加引号；
5. B2 的阶段记录存在**相对链接层级错误**（7 条死链）→ 门禁精确报出文件与目标，逐条修正。

第 5 条尤其值得一提：这类错误人工审查极难发现，而门禁直接列出了具体的错误路径。

### 5.2 未执行（必须如实记录）

| 检查 | 未执行原因 | 恢复验证所需 |
|---|---|---|
| `uv sync` / `uv run`（MAIN 与三个模块） | 沙箱禁止写 `uv` 缓存目录（`D:\dev\caches\uv` 在工作区之外），报 `failed to open file ... os error 5` | 可写缓存的环境 |
| B1 `pnpm install` / `pnpm typecheck` / `pnpm build` | 本阶段未运行构建（`node_modules`/`dist` 存在但未复验） | 执行并记录输出 |
| B1 `pnpm lint` / `pnpm test` | **工具链不存在**：`lint` 仅为 `tsc -b`；无 vitest | 补齐 ESLint 与 vitest，或明确记为遗留项 |
| B2/B3/B4 的 `ruff` / 类型检查 / `pytest` | `uv` 不可用；业务代码为骨架 | 可写缓存的环境，或用各模块 `.venv` 直接运行 |
| PostgreSQL / NATS 启动与 readiness | 本阶段未启动容器 | `docker compose up -d` 后请求 `/health/ready` |
| host 插件装载 | 同上（需运行 host 且安装三个模块） | 见 `../../runbooks/integration.md` |
| B1 静态站点由 host 提供 | 同上 | 构建后访问 `/app` |
| 数据库迁移（各模块 `alembic upgrade head`） | 无可用 PostgreSQL | 见 `../../runbooks/local-development.md` |
| Linux 安全门禁（SEC-01…20）与资源门禁（RES-01…05） | 需 cgroup v2 + nsjail + Linux | `../../runbooks/linux-acceptance.md` |
| 集成与端到端用例（INT-*、E2E-*） | 业务能力为骨架 | 功能阶段逐个实现 |

**明确声明**：以上未执行项**不得**被表述为已通过；它们的存在是保持阶段 `active` 的直接原因。

---

## 6. 风险与遗留项

| 风险/遗留项 | 影响 | 责任 | 目标阶段 |
|---|---|---|---|
| §5.2 的未验证项累积 | 无法声称系统可运行 | MAIN + 各模块 | Phase 01 起逐步消化 |
| B1 缺 lint 与测试工具链 | 前端质量门禁不完整 | B1 | Phase 01 |
| `uv` 在本环境不可用 | 三个后端模块的检查无法在此环境执行 | 环境 | 提供可写缓存的环境 |
| `infra/postgres/init.sql` 无角色供给 | 最小权限无法落地，G-DB-01/02 无从验证 | B4 | Phase 02 |
| 提交物保留期未定值 | 4 GB 磁盘预算不确定 | B2 | Phase 04 |
| 题目图片单图/总量上限未定 | 1.5 GB 预算不确定 | B3 | Phase 03 |
| 承接契约变更的串行点 | 并行度受契约审批约束 | MAIN | 持续（变更规程缓解） |
| 设计期/运行期接口权威的落地校验未实现 | 契约与注册表可能漂移 | MAIN + B4 | Phase 01/02 |
| `ModuleDescriptor.events` 不区分 publishes/subscribes | 无法校验事件方向 | MAIN（SDK） | P3 级契约变更 |
| 沙箱在容器中运行需放宽权限 | 与最小权限原则冲突 | B2 | Linux 验收时定方案 |
| 反作弊（硬编码输出）未覆盖 | 学术诚信风险 | — | 待课程方确认是否属范围 |

---

## 7. 文档影响检查

| 类别 | 本阶段动作 |
|---|---|
| 需求 | 新建 `rq-catalog.md`（32 条逐条验收）；重写 `traceability.md`（**补 `RQ-B-4-99`**）与 `prd.md`/`srs.md` |
| 架构 | 重写 `overview.md`、`models.md`；新建 `security.md`（STRIDE）、`resource-budget.md` |
| API/事件 | 重写 `api/governance.md`；新建 `api/identifiers.md`、`api/events.md`、`contracts/change-protocol.md` |
| 契约 | 扩写全部 OpenAPI/AsyncAPI/Schema；新增 `language.schema.json`、5 份示例、`CHANGELOG.md`；**四份锁同步更新** |
| 数据库 | 重写 `database/ownership.md`（读写矩阵、引用规则、生命周期、表结构规范要点） |
| 测试 | 重写 `strategy.md`、`acceptance.md`；新建 `cases.md`（79 条用例目录） |
| ADR | 重写 ADR-0001/0002；新建 ADR-0003～0007 与索引 |
| 运行手册 | 重写 `local-development.md`；新建 `integration.md`、`linux-acceptance.md`、`troubleshooting.md` |
| 协作 | 新建 `collaboration/module-doc-spec.md`（模块文档规范）、`contribution-workflow.md`、`definition-of-done.md`、`README.md`；重写四份 workstream brief |
| 阶段与知识 | 重写本阶段记录与归档索引；新建知识库索引与首条知识笔记 |
| 模块文档 | 四个模块按 `module-doc-spec.md` 扩建（需求/架构/API/数据库/测试/ADR/运行手册/协作/知识） |

---

## 8. 下一步（Phase 01 入口）

1. 在可写缓存的环境执行 `uv sync` 与各模块 `ruff`/`pytest`，把真实结果回填 §5.2。
2. 补齐 B1 的 ESLint 与 vitest，使 lint 与 test 成为真实门禁。
3. 启动 PostgreSQL/NATS/host，执行 `integration.md` §6 的"即插即用验证规程"。
4. 实现 B4 的角色与 schema 供给，使 G-DB-01/02 可验证。
5. 执行冷启动验收（`../testing/acceptance.md` §4）。
