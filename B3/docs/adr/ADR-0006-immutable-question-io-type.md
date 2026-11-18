# ADR-0006: 题目级 `io_type` 等于 `adapter.kind` 且创建后不可变

- 状态：`接受`
- 关联需求：`RQ-B-3-40/50/60`

## 背景

`b3.question_versions.adapter_manifest` 原先已具备 `kind ∈ {stdin, file, callable}`，但 `b3.questions` 缺少题目级 IO 类型字段，导致题库筛选、统计分组、出题向导引导及跨版本一致性校验缺少显式事实源，且旧修订差异中保留了 `adapter_kind_changed`。

## 决策

1. **单一词表**：`b3.questions.io_type` 使用与 `adapter.kind` 完全一致的三值枚举 `stdin | file | callable`。
2. **创建即锁定，改版不可变**：题目创建时写入 `io_type`；后续创建草稿版本、修改草稿、发布门禁均强制 `version.adapter.kind == question.io_type`，拒绝跨类型改版，并从修订差异（`RevisionDiff`）移除 `adapter_kind_changed`。
3. **存量回填原则**：迁移 `20261118_0014` 仅从最新版本的 `adapter_manifest.kind` 回填，不对缺版本遗留行静默填充默认值（无版本遗留行保留 `NULL` 并输出 R3 警告清单供人工裁定）。
