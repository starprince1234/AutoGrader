# 协作规范（导航）

本目录规定**四组人如何并行开发而不互相破坏**。这是"功能完全解耦 + 并行开发不出乱子"这一目标的制度部分。

## 文件

| 文件 | 作用 | 读者 |
|---|---|---|
| [module-doc-spec.md](module-doc-spec.md) | **模块文档规范（MAIN 强制）**：每个模块必须有哪些文档、每份必须回答什么问题、三条铁律、写作规范、自检清单 | 全体 + 每个模块的负责人 |
| [contribution-workflow.md](contribution-workflow.md) | 日常协作流程：分支命名、需求认领、共享文件冲突预防、契约变更触发条件、提交与验收节奏 | 全体开发者 |
| [definition-of-done.md](definition-of-done.md) | 项目级"完成"的定义与提交前检查清单 | 全体开发者 + 评审者 |

## 配套文件

| 文件 | 作用 |
|---|---|
| [../workstreams/B1.md](../workstreams/B1.md) ～ [B4.md](../workstreams/B4.md) | MAIN 给各模块的**任务书**（需求、边界、依赖、泳道拆分、完成条件） |
| [../contracts/change-protocol.md](../contracts/change-protocol.md) | 契约变更的唯一合法路径 |
| [../requirements/rq-catalog.md](../requirements/rq-catalog.md) | 32 条需求的逐条规范解释与验收标准 |
| [../glossary.md](../glossary.md) | 全局术语（避免各模块自造词） |
| 各模块 `docs/collaboration/` | 模块自己的 onboarding、工作包、接口摘要、DoD、术语 |

## 并行开发的基本形态

```text
         ┌──────────────── MAIN 契约基线冻结（0.1.0）────────────────┐
         │                                                          │
         ▼                ▼                ▼                ▼
      B1 开发线         B2 开发线         B3 开发线         B4 开发线
      （UI）           （评测）          （题库）          （平台）
         │                │                │                │
         └────────────────┴───────┬────────┴────────────────┘
                                  ▼
                    只有契约变更需要串行审批
                    （走 change-protocol.md）
                                  │
                                  ▼
                        集成运行与端到端验收
```

**并行成立的三个前提**（缺一不可）：

1. **契约已冻结**：模块以"契约已锁定"为开工前提，不需要等对方实现完成。
2. **边界清晰**：每项需求有唯一 owner；越界可被机械判定（是否修改了别人的数据/schema/契约）。
3. **冲突有协议**：共享文件、契约变更、需求归属变更都有明确流程，不靠临时协商。
