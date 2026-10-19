# 架构决策记录（ADR）索引

ADR 记录**跨模块、修改代价高、或后人会问"为什么这样做"**的决策。日常实现细节不进 ADR（进阶段文档或知识笔记）。

## 状态说明

| 状态 | 含义 |
|---|---|
| 提议 | 已提出，尚未生效 |
| 接受 | 已生效，当前实现必须遵守 |
| 取代 | 已被更新的 ADR 取代（保留原文，不静默重写） |
| 拒绝 | 经过评估后未采纳（保留原因，避免重复讨论） |

## 决策清单

| 编号 | 标题 | 状态 | 影响范围 | 关联需求 |
|---|---|---|---|---|
| [ADR-0001](ADR-0001-plugin-composition-and-schema-ownership.md) | 插件式 composition host 与 schema 所有权 | 接受 | 全体 | `RQ-B-0-10/20`、`RQ-B-4-10/30/40` |
| [ADR-0002](ADR-0002-current-xlsx-requirements-baseline.md) | 当前需求表（XLSX）作为规范基线 | 接受 | 全体 | 全部 32 条 |
| [ADR-0003](ADR-0003-api-authority-and-runtime-registry.md) | 接口权威的双层切分：设计期归 MAIN，运行期归 B4 | 接受 | MAIN、B4 | `RQ-B-4-40` |
| [ADR-0004](ADR-0004-sandbox-technology-selection.md) | 沙箱执行技术选型：nsjail | 接受（含待确认项） | B2 | `RQ-B-2-50/70` |
| [ADR-0005](ADR-0005-deterministic-replayable-scoring.md) | 确定性、可重放的评分：LLM 不参与成绩与反馈 | **部分被 ADR-0009 取代** | B2、B3 | `RQ-B-2-40/50/70/80`、`RQ-B-3-40/50/60` |
| [ADR-0006](ADR-0006-event-driven-decoupling.md) | 事件驱动解耦：at-least-once + outbox/inbox + DLQ | 接受 | 全体 | `RQ-B-3-30`、`RQ-B-4-20` |
| [ADR-0007](ADR-0007-b1-static-client-provided-by-host.md) | B1 采用静态客户端形态，由 composition host 同源提供 | **取代：ADR-0017** | B1、MAIN | `RQ-B-1-10`～`RQ-B-1-99` |
| [ADR-0008](ADR-0008-module-settings-view.md) | 单进程多模块的配置视图：`<MODULE>_` 前缀覆盖同名通用变量 | 接受 | MAIN、B2、B3、B4 | `RQ-B-0-20`、`RQ-B-4-10/30` |
| [ADR-0009](ADR-0009-ai-assistance-two-track.md) | AI 辅助：分数保持确定性，评语与出题辅助走独立轨 | 接受 | B1、B2、B3、MAIN | `RQ-T-10`、`RQ-T-20` |
| [ADR-0010](ADR-0010-sandbox-layering-and-proof.md) | 沙箱隔离：分层强度 + 可证明性（关闭 ADR-0004 待确认项） | 接受 | B2、B4、B1 | `RQ-B-2-40/50/70` |
| [ADR-0011](ADR-0011-cross-module-composition-via-api.md) | 跨模块数据组合：走声明式 API，物理隔离保留 | 接受 | B1、B2、B3、B4 | `RQ-B-1-30/60`、`RQ-B-2-80` |
| [ADR-0012](ADR-0012-responsibility-reassignment-proposal.md) | 职责归属提案：执行与评分从 B2 迁往 B3 | **提议（待课程方批准，不得据此改代码）** | B2、B3、MAIN、B1 | `RQ-B-2-40/50/70` |
| [ADR-0013](ADR-0013-open-module-count.md) | 模块数是开放的：任意 N 个模块，而不是恰好 4 个 | 接受 | MAIN、B2、B3、B4、未来模块 | `RQ-B-4-40` |
| [ADR-0014](ADR-0014-ai-advisory-score.md) | AI 评分参考：顾问性建议，与权威分数物理分离 | 接受 | B2、B1、MAIN | `RQ-T-30` |
| [ADR-0015](ADR-0015-reference-verification-orchestration.md) | 参考解答验证的编排：B2 执行、B3 记录、教师一键触发 | 接受 | B2、B3、B1、MAIN | `RQ-T-10`、`RQ-B-3-20` |
| [ADR-0016](ADR-0016-package-digest-freezes-the-artifact.md) | `package_digest` 冻结编译产物，不在读时重算 | 接受 | B3（实现）、MAIN | `RQ-B-2-80`、`RQ-B-3-50` |
| [ADR-0017](ADR-0017-vercel-frontend-native-api-host.md) | B1 在 Vercel，API 原生运行于 cgroup v2 主机并经 Cloudflare Tunnel 暴露 | 接受 | B1、MAIN、B2、B3、B4、运维 | `RQ-B-0-20`、`RQ-B-1-10`～`RQ-B-1-99`、`RQ-B-2-50/70` |

## 阅读建议

| 你的角色 | 建议先读 |
|---|---|
| 第一次进入项目 | ADR-0002（为什么以 XLSX 为准）→ ADR-0001（整体形态） |
| B1 开发 | ADR-0017、ADR-0001 |
| B2 开发 | ADR-0004、**ADR-0009**（评语轨边界）、ADR-0005（仍有效的确定性部分）、ADR-0001 |
| B3 开发 | ADR-0002（职责冲突证据）、ADR-0009（出题辅助 `RQ-T-10`）、ADR-0006 |
| B4 开发 | ADR-0003（设计期/运行期切分）、ADR-0001、ADR-0006、ADR-0008（配置视图） |
| 评审/答辩 | 全部，尤其 ADR-0002 的冲突表、ADR-0004/0005 的取舍、**ADR-0009 的两轨取舍** |

## 已识别但**尚未**决策的议题

下列议题已被识别，但**还没有**对应的 ADR，因此当前**没有**约束力。在决策前，任何模块都不得单方面假定某一种做法：

| 议题 | 现状与影响 | 建议何时决策 |
|---|---|---|
| **是否改用 Firejail/Docker 替代 nsjail** | **已裁决：否**，见 [ADR-0010](ADR-0010-sandbox-layering-and-proof.md)。复评结论：换成 Docker 需要常驻 dockerd（约 100–200 MB RSS），在 512 MB 账本下不可行，而隔离强度并不更高；Firejail 引入 setuid 扩大信任面；gVisor 记为将来可选强化。ADR-0004 的待确认项就此关闭 | 已关闭 |
| **是否去掉跨 schema 强隔离** | **已裁决：保留**，见 [ADR-0011](ADR-0011-cross-module-composition-via-api.md)。跨模块组合改走声明式 API，关联键写进契约（`EnrollmentPublic.student_id` 即落地示例） | 已关闭 |
| **是否把"执行/评分"从 B2 挪给 B3** | **提案已就绪但未生效**，见 [ADR-0012](ADR-0012-responsibility-reassignment-proposal.md)。该职责由课程需求列表（XLSX）划定，改它需要**课程方认可**，否则按 XLSX 判定为两条需求同时未完成。**在批准前不得据此改代码**；替代路（`RQ-T-*` 给 B3 加能力）现在即可执行 | 需课程方确认 |
| 静态客户端 B1 是否纳入插件装载机制 | B1 由 Vercel 构建和交付，不参与 API host 的 capability DAG；线上交付不依赖 `entry_point: static:dist` | ADR-0017 已确定 |
| `ModuleDescriptor.events` 是否拆分为 publishes/subscribes | 当前是扁平元组，无法校验事件发布/订阅方向；`module.yaml` 已区分二者。属 SDK 破坏性变更 | 契约变更阶段（P3） |
| capability 名称是否集中登记 | 目前是自由字符串（`b4-auth`、`b3-questions`…），存在拼写漂移风险 | 功能阶段 |
| 是否正确处理"课程方要求改用 Firejail/Docker" | ADR-0004 已记录待确认事项；若课程方明确要求，需重新评估资源账本 | 尽早向课程方确认 |
| 评测结果保留期与清理策略的具体数值 | 目前只定义了"按课程配置"（`../requirements/srs.md` 6.2）；无具体默认值 | B2 功能阶段 |
| 是否需要"反作弊"（硬编码输出检测） | ADR-0005 明确记录为超出本期的已知限制；若课程要求则需新增需求 | 课程方确认后 |

## 新建 ADR 的流程

1. 确认该决策满足至少一条：影响多个模块 / 影响公共接口 / 影响数据所有权或安全边界 / 未来修改代价高且存在多个可行方案 / 替代既有架构 / 团队之后会问"为什么"。
2. 命名 `ADR-NNNN-short-title.md`（NNNN 为四位编号，取当前最大 +1）。
3. 使用固定结构：`状态` / `背景` / `决策` / `后果` / `关联`。
   - **背景**必须写出**被评估过的备选方案**及其被否原因（本目录的 ADR 均遵循此要求）；只写"我们选了 X"而没有比较过程，视为不合格。
   - **后果**必须同时写获得、付出、遗留风险三类。
4. 更新本文件的清单表。
5. 若新 ADR 取代旧的：把旧 ADR 状态改为"取代"并链接新编号，**不删除也不要静默改写旧结论**。
6. 若决策改变了契约或数据所有权：同时按 `../contracts/change-protocol.md` 执行。
