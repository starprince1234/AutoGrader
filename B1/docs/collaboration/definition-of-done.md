# B1 完成定义（DoD）

一条需求算"完成"必须**全部**满足下列检查项，且每项有可核验动作。

| # | 检查项 | 可核验动作 |
|---|---|---|
| 1 | 代码实现 | 相关组件/页面存在，`pnpm typecheck` 通过 |
| 2 | 测试 | 对应用例（`../testing/cases.md`）真实存在且通过（`pnpm test`，接入 vitest 后） |
| 3 | 无 mock 残留 | 全仓搜索无 mock 成功响应充当交付；mock 仅隔离不可控外部服务 |
| 4 | 无手抄字段 | 无手写契约字段类型/结构（ADR-0002），typecheck 靠生成类型 |
| 5 | 追踪表更新 | `../requirements/traceability.md` 对应行状态与证据位置已填 |
| 6 | 阶段记录 | `../phases/active/` 记录真实命令 + 结果 + 未执行项及原因 |
| 7 | 契约一致 | 未改 `../../../MAIN/contracts/**`；`../../contracts.lock` 摘要一致 |
| 8 | 安全与脱敏自检 | 富文本白名单、无 token/答案进日志/DOM/本地存储、前端隐藏不当作安全边界 |
| 9 | 文档同步 | 受影响架构/API/测试/README 文档已同步 |
| 10 | 验收通过 | `../requirements/acceptance.md` 对应场景全部通过（含失败判定） |

## 提交清单（一条需求的完成产物）

1. 代码变更（组件 + 测试）。
2. `../requirements/traceability.md` 状态改为 `已完成` + 证据位置。
3. `../testing/cases.md` 对应用例标记"存在"。
4. `../phases/active/` 新增验证记录。
5. 认领表（`workpackages.md`）状态更新。
