# 验收门禁

本文件规定**每个阶段在什么条件下才能被判定为完成并可归档**，以及初始化阶段的逐项验收结论。

配套文件：`strategy.md`（测什么）、`cases.md`（用例目录）、`../runbooks/linux-acceptance.md`（Linux 专属验收）、`../phases/`（阶段记录）。

---

## 1. 归档门禁（所有阶段通用）

一个阶段只有**同时**满足下列条件才能从 `phases/active/` 移到 `phases/archive/`：

1. **完成条件全部满足**，且每条都有可指向的证据（命令 + 环境 + 结果）。
2. **静态检查、类型检查、必要的测试与构建真实通过**。
3. **文档已同步**：API、数据库、架构、测试、README、运行手册、追踪矩阵。
4. **没有被隐藏、吞并或降级处理的失败**。
5. **遗留风险已明确记录**，且不构成该阶段完成条件的一部分。
6. **无 mock 交付残留**，无未实现业务路由，无占位响应。
7. **契约版本与摘要一致**（四份锁与实时摘要相等）。

**不满足时**：阶段留在 `active/`，写明已确认事实、阻塞原因、恢复验证所需操作。**不得**仅因代码写完就归档。

---

## 2. 初始化阶段验收（Phase 00）

### 2.1 结论

| 项 | 结论 |
|---|---|
| 阶段状态 | **active（未归档）** |
| 阶段性质的准确表述 | **契约与文档骨架完成，运行验证待执行** |
| 主体成果 | 五个独立项目、MAIN 契约与 composition host、四模块骨架、文档治理体系 |
| 可否归档 | **否**——§2.3 列出的运行时门禁全部未执行 |
| 四个模块可否并行开工 | **可以**（在遵守各模块开工前动作的前提下，见 §2.7） |

> 关于表述的自我更正：本阶段早期曾出现"前置工作已全部完成并通过校验"这类概括。**它是不成立的**——通过校验的只是**契约与文档治理**部分。准确表述如上表第二行：文档与契约骨架完成，而 host 装载、迁移、Compose、Linux 沙箱与资源实测**一项都还没跑**。后续汇报必须沿用这一表述。

### 2.2 已满足的门禁

| 门禁 | 判据 | 证据（真实执行） |
|---|---|---|
| G-01 五个项目独立根 | 各自存在 `.git`、`AGENTS.md`、`README.md`、`.gitignore`、`.env.example` | 目录清点，五个项目均为独立 Git 仓库（尚无提交） |
| G-02 契约可解析且示例有效 | 契约 YAML/JSON 可载入；示例通过归属 schema | `.\.venv\Scripts\python.exe -m autograder_host.checks` → `examples vs schemas: OK` |
| G-03 JSON Schema 合法 | `contracts/schemas/*.json` 均为合法 2020-12 schema | 同上（`Draft202012Validator.check_schema` 全部通过） |
| G-04 模块清单合法且无冲突 | 四个 `module.yaml` 通过 `manifest.schema.json`；路由与 schema 归属唯一 | 同上 → `check_manifests: OK` |
| G-05 四份锁与实时摘要一致 | 版本与 SHA-256 相等 | 同上；摘要 `257198f0b07904a94b5ec5a61b1604fe3a9dff4c5793d887025cb55910839eed` |
| G-06 需求基线完整 | 32 条 `RQ-B-*` 全部在追踪矩阵中（**含 `RQ-B-4-99`**） | 同上 → `check_requirement_baseline: OK` |
| G-07 废弃 B3 路径未在实现面复现 | 源码/迁移/清单**以及编译产物（`.pyc`）**中无旧评分标识 | 同上；字节码扫描已按"植入 → 失败 → 清除 → 通过"实测证明（见 §2.5 R-01b） |
| G-08 模块文档集合完整 | 四个模块均具备 spec 要求的文档与阶段记录 | 同上 → `check_module_governance: OK` |
| G-09 文档编码与链接 | UTF-8、无 BOM、无死链 | 同上 → `check_document_encoding_and_links: OK` |
| G-10 B3 旧设计已从源码移除 | 无 scoring/evidence/report 模型、接口、迁移、测试 | 源码清点；残留字节码已清理 |

**执行命令（真实）**

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

> 说明：正常环境下应使用 `uv run python -m autograder_host.checks`；本机沙箱下 `uv` 无法写入其缓存目录（`D:\dev\caches\uv` 在工作区之外），因此使用仓库内既有虚拟环境执行等价检查。此差异已如实记录。

### 2.3 未执行/未满足的门禁

| 门禁 | 状态 | 原因 | 恢复验证所需 |
|---|---|---|---|
| B2/B3/B4 的 ruff / 类型检查 / pytest | **未执行** | `uv` 在本机沙箱不可用；业务代码为骨架 | 可写 uv 缓存的环境，或在各模块 `.venv` 中直接运行 |
| B1 的 `pnpm lint` / `pnpm test` | **不存在** | 无 ESLint 与 vitest；`lint` 脚本目前仅 `tsc -b` | 本阶段补齐工具链，或明确记录为遗留项 |
| PostgreSQL / NATS 启动与 readiness | **未执行** | 未启动容器；依赖 Docker 环境 | `docker compose up -d` 后请求 `/health/ready` |
| host 插件装载与 live/ready | **未执行** | 同上（需运行 host 且安装三个模块） | 装好各模块后启动 host |
| Vercel + Cloudflare Tunnel 生产入口 | **未执行** | 前端入口由用户确认可访问；API Tunnel 路由、外部 readiness 与业务流程尚未验证 | 访问 `https://www.91idc.me/app/` 与 `https://api.91idc.me/health/ready` |
| Linux 安全门禁（SEC-01…20） | **未执行** | 需 cgroup v2 + nsjail | `../runbooks/linux-acceptance.md` |
| 资源门禁（RES-01…05） | **未执行** | 同上 | 同上 |
| 集成/端到端用例（INT-*、E2E-*） | **未实现** | 业务能力为骨架 | 功能阶段逐步实现 |
| 备份/恢复演练 | **未执行** | 无 `b4` 业务表 | B4 功能阶段 |

**明确声明**：上述未执行项**不得**在任何文档或汇报中被表述为已通过。

### 2.4 阶段完成条件（Phase 00）

初始化阶段的完成条件、以及当前达成情况：

| # | 完成条件 | 达成 |
|---|---|---|
| 1 | 五个项目根结构、Git、`.gitignore`、`.env.example` 齐备 | ✅ |
| 2 | 各模块 `AGENTS.md`、README、docs 索引、`module.yaml`、`contracts.lock` 齐备 | ✅ |
| 3 | 完整文档体系（需求/架构/API/数据库/测试/ADR/运行手册/阶段/协作/知识） | ✅ |
| 4 | 需求目录固化 32 条 RQ 与逐条验收标准 | ✅ |
| 5 | 契约权威完整（OpenAPI/AsyncAPI/JSON Schema/示例）且示例通过实例校验 | ✅ |
| 6 | Platform SDK 协议与 composition host 可运行 | ⚠️ 代码存在，装载**未在真实环境验证** |
| 7 | 契约门禁、需求基线门禁、文档门禁自动化 | ✅ |
| 8 | B3 旧评分设计完整移除（含残留产物） | ✅ |
| 9 | 各模块静态检查与测试真实通过 | ❌ 未执行 |
| 10 | PostgreSQL/NATS/host 统一启动与 readiness 验证 | ❌ 未执行 |
| 11 | 资源实测 | ❌ 未执行 |

→ **结论：条件 9/10/11 未满足，阶段保持 active。**

### 2.5 本阶段发现并处置的问题

| ID | 问题 | 严重性 | 处置 |
|---|---|---|---|
| R-01 | **B3 已删除的旧评分实现的字节码仍留在磁盘上**（`B3/src/autograder_b3/__pycache__/` 下有 `models`/`api`/`schemas`/`services`/`repositories`/`config`/`db`/`main` 的 `.pyc`，含 `EvaluationRun`、`evaluation_runs`、`evidence-scoring` 等字符串），另含已删除的旧迁移与测试的 `.pyc` | 中 | 已删除 19 个孤儿字节码文件；并把"实现面"纳入自动化检查。**教训**：原检查只扫 `*.md`，因此"文档干净"掩盖了"磁盘不干净" |
| R-01b | **R-01 的首次修复不完整，且曾被当成已完成**：初版 `check_deprecated_b3_artifacts()` 的扫描循环带 `continue` 跳过 `__pycache__` 与 `.pyc`——**恰好跳过了事故真正发生的那个面**。字节码是手工删掉的，"下次再出现会被拦住"当时并不成立；`knowledge/` 中那条笔记的预防措施声明也因此是**假的** | **高** | `.pyc` 不再被跳过，改为字节读取 + ASCII 匹配；判据是"被移除的设计是否仍在任何产物面"（无害孤儿不报错）。已按"植入含 `evidence-scoring` 的 `.pyc` → 门禁失败并报出完整路径 → 清除 → 恢复通过"实测证明；知识笔记已更正并标注该次修复不完整 |
| R-10 | 文档**内部自相矛盾**：`B1/docs/collaboration/interfaces.md` 冻结清单被我只改了一半——第 43–47 行已更正为"已冻结"，第 48–52 行仍残留"成绩查询/导出端点未冻结""SSE 事件类型表未冻结"，且末行还写着"标未冻结的项不得开工"，**直接与上表冲突并会阻止 B1-B/B1-C 开工** | **高（会阻塞开工）** | 删除陈旧行，改为单一准确表格；并写明"若与 `../api/contract.md` 不一致须同时修正两处"。同样的"只改一半"错误也在 `B4/docs/api/contract.md` 出现（R-11） |
| R-11 | `B4/docs/api/contract.md` 第 7 行仍写 OpenAPI"仅声明两个 operation"，与已补全的 `b4.yaml` 及**同一文件下方**的表格矛盾 | 中 | 更正为完整 operation 清单，并明确区分"契约已锁定 ≠ 实现已完成" |
| R-02 | `RQ-B-4-99` 在 `traceability.md` 与 `checks.py` 中被遗漏 | 高 | 两处均补齐，并纳入 32 条基线断言 |
| R-03 | `checks.py` 对 B3 废弃标识的检查只覆盖 `*.md`，且会**误伤**合规的"记录已移除"表述 | 中 | 重写为：实现面（`src`/`alembic`/`module.yaml`）全字符串扫描 + 追踪矩阵只拦截"需求声明"式出现 |
| R-04 | 契约示例只校验"是合法 JSON"，未做实例校验；且多数 schema 语义空洞（`type: object` 无约束） | 高 | 扩写 4 个 schema；新增实例校验门禁与 5 份示例 |
| R-05 | `MAIN/infra/postgres/init.sql` 只建 schema，无角色供给 | 中 | 记录为缺口，转 B4 功能阶段 |

### 2.6 遗留风险

| 风险 | 影响 | 责任 | 目标阶段 |
|---|---|---|---|
| 未验证项的累积（§2.3） | 无法声称系统可运行 | MAIN + 各模块 | 功能阶段逐步消化 |
| nsjail 在容器中运行需额外权限 | 与最小权限原则冲突 | B2 | Linux 验收时定方案 |
| `uv` 在本机沙箱不可用 | 各模块检查无法在此环境执行 | 环境 | 提供可写缓存的环境 |
| 设计期/运行期接口权威的落地校验尚未实现 | 契约与注册表可能漂移 | MAIN + B4 | 功能阶段 |
| 提交物保留期未定值 | 磁盘账本不确定 | B2 | 功能阶段 |

---

## 2.7 四模块并行开工条件（开工前动作）

**结论：四个模块均可开工，但不是四组 Agent 随意改。** 每个模块在动手前先完成本模块的开工前动作；MAIN 的契约与摘要仍由 MAIN 单独维护。

| 模块 | 可否开工 | 开工前动作 | 阻塞原因 |
|---|---|---|---|
| **B1** | 可以 | 先修正本模块 `docs/collaboration/interfaces.md` 中"成绩/SSE 未冻结"的陈旧状态（**已由 MAIN 修正，见 §2.5 R-10**）；随后按泳道 A → B/C 推进 | 无（R-10 已解决） |
| **B2** | 可以 | 先冻结 B2 **内部**接口：`SubmissionEnvelope` 内部模型、`run_case()` 签名、静态检查结果结构。**这些是模块内部契约，不是 MAIN 公共契约**，无需走变更规程 | 无 |
| **B3** | 可以 | 先冻结**公开 DTO / 内部 DTO 分层**与 **package compiler 内部接口签名**（B3 自行冻结，不得改 MAIN contracts）；开始实现前补 `uv add --dev "pytest-cov>=6,<7"` | 无 |
| **B4** | 可以 | 先修正 `docs/api/contract.md` 中"仅两个 operation"的陈旧描述（**已由 MAIN 修正，见 §2.5 R-11**） | 无 |
| **MAIN** | 模块 Agent **不得修改** | `MAIN/contracts/**`、SDK、版本与摘要由 MAIN 单独维护 | — |

**四个 Agent 都必须遵守的边界**：

1. 不修改 `MAIN/contracts/**`；
2. 不修改**其他模块**目录；
3. 不直接读取其他模块的数据库 schema（跨模块只能走契约）；
4. 不修改 `contracts.lock`；
5. 契约有问题只能**报告或提交变更说明**（`docs/contracts/changes/`），不能私自"顺手修契约"；
6. 不提交 Git、不创建远端、不创建 CI；
7. 每次完成必须同步本模块文档、需求追踪、测试与 `phases/active/` 记录；
8. 不能用 mock 成功响应冒充真实功能。

### B2 沙箱的推荐实施顺序（先平台无关，后 Linux 专属）

B2 **可以现在开工**，不必等 Linux 环境：

1. 先实现**平台无关**部分：runner 接口、三种 adapter 的执行对接、比较器、报告生成、持久化、失败分类。
2. 再实现 **nsjail + cgroup v2 + seccomp** 后端。
3. **不允许**因为 nsjail 不可用而降级到裸进程执行；沙箱不可用时必须**拒绝执行动态评测**（`/architecture/security.md` 的 SEC-20）。
4. 等课程方确认 Firejail/Docker 要求后，再决定是否新增 runner backend（`ADR-0004` 已保留该替换点）。

理由：比较器与报告的正确性可以在任何平台验证，而沙箱只能在 Linux 验证；把两者解耦可以让 B2 在等待 Linux 环境时持续推进，同时不牺牲安全语义。

---

## 3. 后续阶段的验收门禁（预先约定）

| 阶段 | 关键门禁 |
|---|---|
| Phase 01 契约与骨架硬化 | SDK 装载验证；host live/ready 实测；B1 工具链补齐；`uv` 环境下的各模块 lint/test 真实通过 |
| Phase 02 平台底座（B4） | 角色与 schema 供给；跨 schema 拒绝实测；备份恢复演练；名单导入 E2E-02…06 |
| Phase 03 题库（B3） | 公开 DTO 字段缺失断言；版本不可变性；三类 adapter 各一例；统计去标识化与重放 |
| Phase 04 评测（B2） | 状态机；静态→安全阻断；nsjail 安全门禁 16 项；可重放性；导出防注入 |
| Phase 05 UI（B1） | 角色路由与越权；富文本 XSS；提交与 SSE；成绩查询与导出体验 |
| Phase 06 集成与验收 | INT-*、E2E-* 全量；资源实测；文档与契约门禁；冷启动验收 |

---

## 4. 冷启动验收（最终且最容易被忽略的一项）

**判据**：一个不了解本项目的人，**只**阅读下表中的文件，即可完成全部动作。

| 角色 | 只读这些文件 | 必须能做到 |
|---|---|---|
| 任一模块的新成员 | 本模块 `README.md` + `AGENTS.md` + `docs/collaboration/onboarding.md` | 装依赖、跑通本模块门禁、说出契约版本、认领一个工作包 |
| B1 成员 | 上述 + `../MAIN/docs/workstreams/B1.md` | 知道自己的边界（无权威数据）与依赖的端点 |
| B2 成员 | 上述 + `workstreams/B2.md` + `ADR-0004`/`ADR-0005` | 知道沙箱与评分原则，不越界碰题目与身份 |
| B3 成员 | 上述 + `workstreams/B3.md` + `ADR-0002` | 知道职责冲突以 XLSX 为准，且自己不执行、不算分 |
| B4 成员 | 上述 + `workstreams/B4.md` + `ADR-0003` | 知道设计期/运行期的切分 |
| 评审者 | `MAIN/README.md` + `docs/requirements/prd.md` + `docs/adr/README.md` | 说出产品定位、创新点、关键取舍与已知限制 |

**当前状态**：模块侧文档正在按 `../collaboration/module-doc-spec.md` 建设；MAIN 侧已就绪。冷启动验收**尚未执行**，需在文档建设完成后由一次真实试读来验证。
