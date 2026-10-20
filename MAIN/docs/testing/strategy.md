# 测试与质量策略

本文件规定**测什么、在哪一层测、用什么基础设施、门禁命令是什么、什么不允许**。

原则：**测试覆盖真实风险，不为每个文件机械建测试。** 公共契约、序列化、数据库迁移、权限、安全边界、状态机、可重放性——这些必须有测试；一个纯 getter 不需要。

---

## 1. 测试分层

| 层 | 目标 | 范围 | 基础设施 | 责任 |
|---|---|---|---|---|
| **单元** | 纯逻辑正确性 | 比较策略、评分合计、规则匹配、解析器、状态机迁移函数 | 无 | 各模块 |
| **契约** | 契约与实现一致 | schema 校验、公开 DTO 字段存在性/缺失性、错误响应形状、事件 payload | 无（或仅 schema 文件） | 各模块 + MAIN |
| **集成** | 跨组件在真实依赖上工作 | 迁移、权限、事务、outbox/inbox、JetStream、SSE、插件装载 | **真实 PostgreSQL + NATS** | 各模块 + MAIN |
| **端到端** | 用户旅程成立 | 登录 → 导入名单 → 发布题目 → 提交 → 评测 → 查分 → 导出 | 全套（Linux + 沙箱） | MAIN 主导 |
| **安全** | 越权与沙箱边界成立 | 越权、注入、沙箱逃逸、资源限制、脱敏 | Linux + cgroup v2 + nsjail | B2/B4 |
| **可重放** | 同摘要同结果 | 五元摘要一致 → 同分同证据 | 全套 | B2 |
| **资源** | 预算成立 | RSS / CPU / 磁盘实测 | Linux + Compose | MAIN |
| **文档** | 治理成立 | 需求覆盖、锁一致、链接有效、编码 | 无 | MAIN（已自动化） |

---

## 2. 各层必须覆盖的风险

| 风险 | 最低层级 | 说明 |
|---|---|---|
| 提交状态机非法迁移 | 单元 + 契约 | 尤其是 `rejected`/`failed`/低分不混淆 |
| 静态高危未阻断动态执行 | 单元 + 集成 | 必须能证明**未进入沙箱** |
| 超时后进程树残留 | 集成（Linux） | 执行后检查进程表 |
| 资源限制未生效 | 安全（Linux） | 用真实超限样本，不是"配置了就算" |
| 沙箱禁网未生效 | 安全（Linux） | 样本内尝试联网必须失败 |
| 幂等键失效导致重复评测 | 集成 | 同一键两次 → 同一 `submission_id`，只有一个 run |
| 事件重复消费导致重复计数 | 集成 | 重投同一 `event_id` 统计不变 |
| 事件乱序导致状态回退 | 集成 | 先收 `completed` 再收 `started` 不得出错 |
| 公开 DTO 泄露参考答案/隐藏用例 | 契约 | **断言字段缺失**，不是人工审阅 |
| 跨 schema 越权访问 | 集成（真实 PG） | 数据库必须拒绝 |
| 超级用户连接 | 契约 + 启动校验 | 配置为超级用户 → 拒绝启动 |
| 目录穿越写文件 | 单元 + 集成 | `..`、绝对路径必须被拒 |
| shell 注入 | 单元 + 集成 | 参数含元字符仍按字面传递 |
| 导出公式注入 | 单元 | `=`/`+`/`-`/`@`/Tab/CR 前缀被转义 |
| 名单静默丢行 | 集成 | 被拒行必须出现在结果中 |
| 名单确认不原子/不幂等 | 集成 | 并发确认与重复确认 |
| 版本可被原地修改 | 集成 | 改已发布版本 → `409` |
| 分数合计不等于细节之和 | 单元 | 合计校验是断言 |
| 同摘要不同分（可重放性破坏） | 可重放 | 重跑比摘要 |
| 日志泄露 token/源码/答案/PII | 契约 | 扫描日志样本 |
| 死链与文档漂移 | 文档（已自动化） | `checks.py` |
| 资源超预算 | 资源（Linux） | `docker stats` + cgroup |

---

## 3. 基础设施要求（硬规则）

1. **不得用 SQLite 代替 PostgreSQL 验收**。SQLite 没有 schema 级权限、没有 `timestamptz` 语义、没有并发事务行为——用它测"跨 schema 拒绝"永远测不出问题。
2. **mock 只允许隔离不可控外部服务**（IMAP 服务器、时钟）。**不允许**用 mock 证明"真实集成已通过"。
3. **不用 mock 证明成功路径**：提交、评测、评分、导出必须在真实数据库上跑。
4. **测试数据不落生产身份**：使用合成名单与合成学号。
5. **测试里不得包含真实凭据**。
6. 安全与资源门禁**必须在 Linux** 完成；Windows 结果不得用于宣称这些能力通过。

---

## 4. 门禁命令

### MAIN

```powershell
# 目前可执行（使用仓库内已存在的虚拟环境；uv 在本机沙箱下无法写缓存）
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
.\.venv\Scripts\python.exe -m pytest

# 正常环境下（需 uv 缓存可写）
uv sync --all-groups --extra modules
uv run python -m autograder_host.checks
uv run pytest
uv run ruff check .
```

### B1

```powershell
cd B1
corepack enable
pnpm install
pnpm typecheck     # 已接入
pnpm build         # 已接入
# 以下尚未接入，属本阶段待补：
# pnpm lint        # 当前 lint 脚本只是 tsc -b，没有 ESLint
# pnpm test        # 没有 vitest / 组件测试
```

### B2 / B3 / B4

```powershell
cd B2   # 或 B3 / B4
uv sync --all-groups --extra modules
uv run ruff check .
uv run pytest
uv run alembic upgrade head    # 需可用的 PostgreSQL
```

> ⚠️ **本机沙箱限制（已不复现）**：早期 `uv` 无法写缓存目录 `D:\dev\caches\uv`（在工作区之外）。该限制后来消失，`uv run ...` 已可正常执行；仍可继续用仓库内 `.venv` 运行等价检查。任何一条没跑的命令**不得**写成"已通过"。

### 4.1 工具链一致性

| 项 | B1 | B2 | B3 | B4 |
|---|---|---|---|---|
| 测试框架 | vitest（jsdom + Testing Library） | pytest | pytest | pytest |
| 覆盖率插件 | （vitest 内置 `--coverage`，未接入） | `pytest-cov` | `pytest-cov` | `pytest-cov` |
| Lint | ESLint（flat config，`--max-warnings 0`） | ruff | ruff | ruff |
| 类型/构建门禁 | `tsc -b` + `api:check` + `vite build` | — | — | — |

**B3 的 `pytest-cov` 曾缺失**：`B3/pyproject.toml` 的 `dev` 组当时只有 `pytest` 与 `ruff`，`uv.lock` 中也没有该包。

**处置（已完成）**：待 `uv` 可用后按 `uv add --dev "pytest-cov>=6,<7"` 的等价结果补齐 `pyproject.toml` 与 `uv.lock`。实测：

```text
uv run pytest -q --cov=autograder_b3 --cov-report=term
TOTAL   3206   324   90%
282 passed, 6 warnings in 38.53s
```

`pytest-cov 6.3.0` / `coverage 7.16.1` 已在 B3 的虚拟环境中安装，四模块覆盖率工具链现已一致。

**结论**：本项目**不以行覆盖率作为质量指标**（见 §8），该插件只用于开发期自查。

---

## 5. 契约门禁的自动化内容（已实现）

`host/src/autograder_host/checks.py` 是按需执行的专项审计，不阻塞日常发布。全模块测试记录由 `scripts/test_evidence.py` 按需生成，不要求每次契约变化重跑全套。当前审计内容：

| 检查 | 内容 |
|---|---|
| `check_contract_examples` | 每份示例是合法 JSON，**且通过其归属 schema 的实例校验**；声明的示例必须存在 |
| （schema 循环） | `contracts/schemas/*.json` 都是合法的 JSON Schema 2020-12 |
| `check_manifests` | 四个 `module.yaml` 通过 `manifest.schema.json`；路由与 schema 无重复归属；四份 `contracts.lock` 的版本与摘要等于实时摘要 |
| `check_requirement_baseline` | 32 条 `RQ-B-*` 全部出现在追踪矩阵（**含 `RQ-B-4-99`**）；被移除的 B3 评分标识未在实现面复现——**包括编译产物 `.pyc`**（判据是"被移除的设计是否仍在任何产物面"，而非"是否存在 `.pyc`"） |
| `check_module_governance` | 四个模块均具备必需的文档集合与阶段记录 |
| `check_document_encoding_and_links` | Markdown 为有效 UTF-8、无 BOM、相对链接不失效 |
| `check_shared_vocabularies` | **被两处声明的值集必须一致**。OpenAPI 无法 `$ref` 进 `schemas/*.json`（不同方言、不同文档），因此两边都要命名的词表必然写两遍（`language` 一直如此；`CHG-0019` 加了 `failure_category` 与 `adapter_kind`，后者是 OpenAPI↔OpenAPI 的重复——不能跨引 `b3.yaml`，因为 B2 的契约测试只从 `common.yaml` 解析外部引用）。此前**没有任何门禁比对这些副本**，单侧修改会静默漂移。可空性**单独**比对：同一个可空性两种拼法都合法（JSON Schema 把 `null` 列为 `enum` 成员；OpenAPI 3.1 之前风格的写法用 `anyOf`），逐字比对会把拼法差异误报成分歧——**会乱叫的门禁会被关掉**。两侧都剥掉 `null` 再比集合，比的是值集本身。**新增一处重复词表时，必须在同一次变更里把该对登记进 `SHARED_VOCABULARIES`** |

**尚未实现**：OpenAPI/AsyncAPI 的**元 schema（meta-schema）校验**；跨 schema FK 检测（需数据库连接）；`module.yaml`/`descriptor`/注册表三方一致性校验（需运行 host）。

> 更正一处过期论断（2026-09-20）：本行此前写"OpenAPI/AsyncAPI 的结构解析校验（目前只做 YAML
> 载入）"。**重复映射键的严格拒绝已由 `check_contract_parses_strictly()` 实现**——PyYAML 会
> last-one-wins 地静默接受重复键，而 js-yaml（B1 的类型生成器在用）直接拒绝，该门禁把门禁
> 保持在"至少和最严格的消费者一样严格"。仍未实现的是**元 schema 校验**（用 OpenAPI 规范本身
> 校验文档），上面已改为准确表述。

---

## 6. 缺陷处理流程

1. **集中分析根因**，不要"看到红就改"。
2. 修复后**只重跑受影响的最小测试集**，再执行必要的最终门禁。
3. 回归测试**必须**加在修复的同一改动批次里，否则缺陷会复发。
4. 同一根因**至少出现两次**且已有稳定预防措施时 → 写入 `../knowledge/`。
5. 测试通过前不得把阶段标为完成。

---

## 7. 明确禁止的"验收伪装"

| 伪装 | 为什么不行 |
|---|---|
| 用 mock 结果证明真实集成通过 | mock 证明的是 mock 的行为 |
| 用 SQLite 结果代替 PostgreSQL 验收 | 权限与事务语义不同 |
| 把"测试收集成功"说成测试通过 | 收集 ≠ 执行 |
| 把静态检查说成运行时功能已验证 | 静态检查不执行代码 |
| 用计划/设计描述成已完成 | 计划不是证据 |
| 在 Windows 上宣称 Linux 专属能力已验收 | 沙箱与 cgroup 在 Windows 不存在 |
| 把超时/失败的验证写成"基本可用" | 阶段归档门禁会拦截 |
| 只在"happy path"上测 | 失败路径才是系统质量的所在 |

---

## 8. 覆盖率与度量的态度

本项目**不追求行覆盖率数字**。度量用于**发现盲区**，不是用于汇报：

| 度量 | 用途 |
|---|---|
| 需求覆盖率（32 条 RQ 有测试项） | **主指标**——比行覆盖重要得多 |
| 安全门禁通过率（16 项） | 安全是否真的成立 |
| 可重放一致性 | 核心承诺是否成立 |
| 资源实测值 / 预算 | 架构是否可行 |
| 契约漂移次数 | 协作流程是否健康 |

**行覆盖率低于某个数字不算缺陷；某条 RQ 没有对应测试项才是缺陷。**
