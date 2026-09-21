# ADR 0003 名单导入管道（不确定即报错）

- **状态**：接受（已实现并实测，见 `../phases/active/phase-01-platform-implementation.md`）
- **背景**：
  - `RQ-B-4-50` 配合 `RQ-B-1-10` 要求：解析 PDF/XLSX 名单 → 列映射 → 逐行校验 → 预览 → 人工确认 → 原子写入；确认前零写入；确认幂等；被拒行不静默丢弃。
  - PDF/XLSX 解析不确定性高，若"自动猜测列、静默丢行"，会导入错误数据且难追溯。
- **决策**：
  1. 管道顺序固定：**解析 → 映射 → 校验 → 人工确认 → 原子写入**（状态机见 `../architecture/models.md`）。
  2. **不确定即报错**：列名无法唯一映射 → 状态 `mapping_required`，不自动猜测，可人工指派列。
     - 状态取值以 `../../MAIN/contracts/openapi/b4.yaml` 的 `RosterImportPreview.status` 枚举为唯一权威；
       本 ADR 此前写作 `mapping_failed`，与契约枚举不符，已按契约统一为 `mapping_required`
       （同一份 ADR 不保留第二套状态词）。
     - 结构性表达：歧义/缺失的字段**不出现在** `proposed_mapping` 中，`detected_columns` 列出实际列。
       实现见 `src/autograder_b4/roster/mapping.py`（`propose_mapping` 只在唯一匹配时才提议）。
  3. 逐行校验错误**进预览**（逐行标红+原因），不阻断整体；确认后**被拒行必须出现在结果报告**，不静默丢弃。
  4. 确认前 `b4.roster_import_rows`（暂存）之外**零名单写入**——`b4.enrollments` 只在确认时被写入；
     确认操作**幂等**（重复确认返回同一结果，且不写第二次）。
  5. 只有写入成功进入 `committed` 后才入队 `autograder.v1.roster.import.completed`（不含 PII），且与
     业务写入**同一事务**（outbox）。
- **后果**：
  - 获得：错误可见、可追溯、可重放；不会静默写入脏数据。
  - 付出：需要人工确认步骤（不可全自动）；状态机与错误分类需实现与测试（已落地）。
  - 遗留风险：解析器对异常 PDF 的鲁棒性以真实样本验证过（文本 PDF、无表头 PDF、无文本 PDF 三类），
    但**未覆盖扫描件 OCR**——扫描件明确 `failed_parse`，不做 OCR；错误分类粒度已与 B1 预览契约对齐，
    但"人类可读的映射原因"在契约中无字段可放（见 `../api/contract.md` 复核发现 3）。
- **关联**：
  - 需求：`RQ-B-4-50`、`RQ-B-1-10`
  - 模型：`../architecture/models.md`（状态机）
  - 测试：`../testing/cases.md`（`T-ROSTER-*`）、`tests/test_roster_import.py`
  - 契约：`../../MAIN/contracts/schemas/roster-import.schema.json`
