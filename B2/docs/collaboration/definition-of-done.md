# B2 完成定义（Definition of Done）

一项需求（RQ）算 `已完成`，必须逐条通过下列可核验动作。任一项不满足，状态只能停留在 `待实现` 或 `阻塞`。

## 代码

- [ ] 有可运行实现，`router()` 中注册了对应端点，或对应组件可被调用（**禁止** mock 充当交付）。
- [ ] 启动参数为固定数组，无 shell 字符串执行。
- [ ] 不 import B3/B4 的 ORM/Python 包，不读 `b3`/`b4` schema。
- [ ] 无兼容层 / 字段别名 / 双写 / 隐藏开关。

## 测试

- [ ] 对应 [../testing/cases.md](../testing/cases.md) 的用例由"待建"改为"存在"，且真实通过（`uv run pytest`）。
- [ ] 恶意样本 / 沙箱违规证据来自**真实执行**，非静态断言。
- [ ] 契约测试（schema/错误码）通过；集成测试使用真实 PostgreSQL（**非 SQLite**）。

## 迁移

- [ ] 表结构变更都有迁移文件；`uv run alembic heads` 与 `module.yaml: database.migration_head` 一致。
- [ ] `upgrade()` 配 `downgrade()`（无法回滚须在迁移 docstring 说明）。

## 文档

- [ ] [traceability.md](../requirements/traceability.md) 对应 RQ 状态置 `已完成`，证据位置指向真实测试与阶段证据。
- [ ] [acceptance.md](../requirements/acceptance.md) 对应场景有通过证据。
- [ ] 新增端点/事件关系时同步 [../api/contract.md](../api/contract.md)、[../api/events.md](../api/events.md)。
- [ ] 新增表/约束意图时同步 [../database/schema.md](../database/schema.md)。
- [ ] [../README.md](../README.md) 状态表同步；[../README.md](../README.md) 索引无遗漏。

## 阶段记录

- [ ] [../phases/active/](../phases/active/) 记录了真实命令 + 环境 + 结果 + 未执行项及原因，无隐藏失败。

## 契约一致性

- [ ] `descriptor` 与 `module.yaml` 一致（`descriptor.events` 缺口**已于 2026-09-16 解除**——不再记为 `阻塞`；
  见 [../README.md](../README.md) 能力状态表与 [../api/events.md](../api/events.md) 第 4 节，本条现按"一致"检查）。
- [ ] 未手抄契约字段表。

## 安全与脱敏自检

- [ ] 日志/事件/导出无源码、参考答案、隐藏用例、凭据、内部绝对路径、超范围 PII。
- [ ] 有脱敏规则测试；安全结论能区分"未发现"与"未检查"。

## 无 mock 残留

- [ ] 全仓搜索无"假评测""固定分数""跳过执行"等 mock 交付路径。

## 冷启动

- [ ] 按 [onboarding.md](onboarding.md) 的 5 步可跑通（装依赖、门禁、契约版本、认领）。
