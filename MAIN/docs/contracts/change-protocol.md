# 契约变更规程（Mandatory）

`MAIN/contracts/**` 是接口的**唯一权威**。本规程规定"从想让接口变一下"到"四个模块都对齐"的完整路径。

**核心原则：契约先行。** 先改契约并升版本，再改实现。任何"先改代码后补文档"的行为都会导致集成门禁失败。

---

## 1. 变更分级

| 级别 | 判定 | 处理要求 | 版本影响 |
|---|---|---|---|
| **P1 增补** | 新增可选字段、新增端点、新增事件、新增可选枚举值 | 更新契约 + 示例 + 消费者文档；不阻塞他人 | MINOR：`0.1.0 → 0.2.0` |
| **P2 语义** | 字段含义变化、必填性变化、错误码语义变化、默认值变化、比较策略变化 | 需变更提案 + 影响分析 + 全部消费者确认 | MINOR 或 PATCH（按是否破坏兼容） |
| **P3 破坏** | 删除/重命名字段或端点、改类型、改事件 subject、改状态机取值、改认证方式 | 需新 ADR + 全体同步迁移 + 一次性切换（**不留兼容层**） | MAJOR：`0.2.0 → 1.0.0` |
| **P4 文档** | 只改 `MAIN/docs/**`、模块 `docs/**`，不动 `contracts/**` | 按 `module-doc-spec.md` 直接更新 | 无版本变化 |

> 判定有疑问时按更高级别处理。

---

## 2. 标准流程（P1～P3 通用）

### 第 1 步：提出（任何人）

在 `MAIN/docs/contracts/changes/` 下新建一份变更记录：

`docs/contracts/changes/CHG-<序号>-<短标题>.md`

内容必须包含：

```md
# CHG-0001 变更标题
- 级别：P1 / P2 / P3
- 提出人 / 日期：
- 动机：要解决什么真实问题（不接受"顺手改一下"）
- 影响的契约文件：逐个列出
- 影响的模块与具体影响：B1/B2/B3/B4 各自要改什么
- 是否破坏兼容：是/否；若是，迁移方式（一次性切换，不保留兼容层）
- 示例载荷变更：前后对比
- 回滚方式：
```

### 第 2 步：影响分析（MAIN）

MAIN 负责确认：

1. **消费方清点**：哪些模块提供/消费该接口或事件（查四个 `module.yaml` 的 `routes`/`events` 与各模块 `docs/collaboration/interfaces.md`）。
2. **安全与隐私检查**：新字段是否可能携带源码、参考答案、隐藏测试、凭据或超范围 PII？若是，拒绝或要求改为不携带。
3. **资源账本检查**：是否显著增加内存/CPU/磁盘占用？
4. **需求一致性检查**：是否与 `rq-catalog.md` 冲突或使某条 RQ 的验收无法成立？

### 第 3 步：改契约（MAIN 独立完成）

在**一个改动批次**内完成：

1. 修改 `MAIN/contracts/openapi/*.yaml`、`asyncapi/events.yaml`、`schemas/*.json`。
2. 更新 `MAIN/contracts/examples/*.json`，并确保示例通过其 schema 校验。
3. 更新 `MAIN/contracts/CHANGELOG.md`（追加一条：级别、版本、变更摘要、CHG 编号）。
4. 提升版本号：所有 `openapi/*.yaml` 与 `asyncapi/events.yaml` 的 `info.version` **必须一致**。

### 第 4 步：重算摘要（MAIN）

摘要算法（`host/src/autograder_host/checks.py` 的 `contract_digest()`）：

```
对 contracts/ 下所有文件按相对路径 POSIX 字符串排序：
  digest.update(相对路径.encode())
  digest.update(b"\0")
  digest.update(文件字节)
取 SHA-256 十六进制
```

执行并记录新摘要：

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

### 第 5 步：更新四个模块的锁（MAIN，一次性）

四个模块的 `contracts.lock` 中的 `digest` 与 `contract_version` 必须同时更新为第 4 步的结果。**遗漏任何一个模块都会导致集成失败**，这是设计意图（防止"某人偷偷改了契约"）。

更新后再次运行 checks 确认通过。

### 第 6 步：各模块实现（并行）

四个模块各自：

1. 按新契约更新实现。
2. 更新本模块 `docs/api/contract.md`、`docs/api/events.md`（只写"我提供/我消费"的关系，不抄字段）。
3. 更新本模块 `docs/requirements/traceability.md` 与 `docs/phases/active/` 记录。
4. 跑通本模块门禁。

### 第 7 步：验证门禁

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks   # 契约 + manifest + 需求基线
.\.venv\Scripts\python.exe -m pytest                    # host 测试
```

通过判据：

- 契约文件可解析；
- 所有示例通过对应 JSON Schema；
- 四个 `module.yaml` 通过 `manifest.schema.json`；
- route / schema 无冲突；
- 四个 `contracts.lock` 的版本与摘要与实时摘要一致；
- 需求编号基线完整（含 `RQ-B-4-99`）。

### 第 8 步：归档变更记录

把 CHG 记录标记为"已完成"，并在 `MAIN/docs/contracts/changes/README.md` 索引中登记。

---

## 3. 硬性禁止

1. **禁止**模块自行修改 `MAIN/contracts/**`。
2. **禁止**保留兼容层：不允许旧字段别名、双写、隐藏开关、自动降级、"为了兼容先留着"。破坏性变更必须一次性切换。
3. **禁止**模块文档手抄契约字段表（会造成静默漂移）。
4. **禁止**修改契约而不重算摘要；也**禁止**为了让检查通过而手动填一个"看起来对"的摘要——摘要必须由 `checks.py` 计算得出。
5. **禁止**在未更新 CHANGELOG 的情况下改契约内容。
6. **禁止**在事件或公开 DTO 中新增可能携带源码/答案/隐藏测试/PII 的字段。

---

## 4. 常见场景速查

| 场景 | 正确做法 |
|---|---|
| 我需要一个契约里没有的字段 | P1：提交 CHG → MAIN 加字段 → 升 MINOR → 我实现 |
| 我想把一个字段从必填改成可选 | P2：确认所有生产方都能容忍缺失；否则改为"始终提供" |
| 我要删掉一个没人用的端点 | 仍需 P3：删契约 + 全仓搜索确认无消费者 + 记录 |
| 我发现契约与实现不一致 | 不要"顺着实现改契约"，也不要"偷偷改实现对齐"。提交 CHG，由 MAIN 判定哪边是对的 |
| 我只是补文档 | P4：直接改，不动 `contracts/`，不用重算摘要 |
| 事件 payload 需要带题目内容 | 拒绝：改为只带 `question_version_id`，由消费方按需拉取 |
| B2 与 B3 想私下达成一个"内部接口" | 必须写进 `MAIN/contracts/`（内部端点也要有契约），否则集成时无法校验 |

---

## 5. 变更记录的当前目录

**唯一权威索引是 [changes/README.md](changes/README.md)**（逐份提案的编号、级别、状态与裁决结论）。
本文件**不再复制**那张表。

> 这里此前复制了一份 5 行的目录表，并在下面写着"本表与索引重复时以 `changes/README.md` 为准"。
> 一句"以另一个为准"并不能阻止漂移，它只是承认了漂移：等 CHG 增加到 8 份、其中 6 份已生效之后，
> 这张表仍然写着 5 份、全部"提议（待 MAIN 判定）"。于是同一份仓库里，"契约改了吗"有两个互相矛盾的答案。
> 按本项目的单一权威规则（`../collaboration/module-doc-spec.md`），这类事实只允许有一个位置。

契约版本随每次生效的变更递增，最终版本与摘要以四份 `contracts.lock` 为准（摘要由
`scripts/relock_contracts.py` 计算，**不得手填**）。
