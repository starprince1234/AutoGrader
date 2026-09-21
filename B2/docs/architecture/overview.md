# B2 架构总览

## 位置（上下文级）

```text
B1 static UI ── HTTP/SSE ── MAIN host
                              ├── B4 identity/course ── PostgreSQL b4
                              ├── B3 question-bank ──── PostgreSQL b3
                              └── B2 evaluation ─────── PostgreSQL b2 ── nsjail
                                                         └── NATS JetStream
```

B2 是 MAIN 组合 host 下的一个插件，通过 entry point `autograder.modules → b2` 被装载。它**从不导入** B1/B3/B4 的代码，只经版本化 HTTP / 事件契约交互（见 [../../../MAIN/docs/architecture/overview.md](../../../MAIN/docs/architecture/overview.md)）。

## 内部层次与依赖方向

```text
┌─────────────────────────────────────────────────────────────┐
│ 接收层 (receiver)   API 端点 + IMAP 适配器                   │
│   ├─ 信封校验、SHA-256 核对、幂等、作用域校验                │
│   └─ 归一化为内部 SubmissionEnvelope                        │
├─────────────────────────────────────────────────────────────┤
│ 存储层 (storage)    内容寻址 artifact + 元数据（b2 schema）  │
├─────────────────────────────────────────────────────────────┤
│ 静态检查器 (static)  Python ast / Bash bash -n + 规则       │
│ 安全判定 (security)  静态高危模式 + 沙箱违规 → finding       │
├─────────────────────────────────────────────────────────────┤
│ runner (nsjail)     按 package 逐用例执行                    │
│ 比较器 (comparator)  实际 vs 期望，输出 CaseEvidence         │
├─────────────────────────────────────────────────────────────┤
│ 报告器 (reporter)   总评 + 细评 + 绑定摘要 → report          │
│ 导出器 (exporter)   XLSX/CSV 导出（防公式注入）              │
└─────────────────────────────────────────────────────────────┘
```

依赖方向自上而下单向：`receiver → storage → static/security → runner → comparator → reporter → exporter`。上层不依赖下层内部实现，只依赖接口签名。具体组件职责见 [components.md](components.md)。

## 主要数据流

```text
B1 ──POST /api/v1/submissions──▶ 接收层 ──▶ 校验/幂等/作用域
                                       │ (202 + submission_id)
                                       ▼
                                 存储层（内容寻址，先写内容后写元数据）
                                       │
                                       ▼ 发布 autograder.v1.submission.accepted（不含源码）
                                 静态检查器 + 安全判定（静态）
                                       │ 高危？
                              ┌────────┴────────┐
                            是（阻断）         否
                              ▼                ▼
                      置 rejected       evaluation_run 进入 queued
                       （不动态执行）          │
                                              ▼
                                     runner 置 running（nsjail 逐用例）
                                              │
                              ┌───────────────┼───────────────┐
                              ▼               ▼               ▼
                         completed       failed          rejected
                         （评分+报告）  （技术性失败）   （执行期安全违规）
                                              │
                                              ▼
                                     reporter 生成报告（每分可追溯）
                                     exporter 按课程/班级导出
```

## 与其他模块的交互方式

| 邻居 | 交互方式 | 说明 |
|---|---|---|
| B1 | HTTP（提交）、SSE（进度）、HTTP（报告/成绩查询、导出下载） | B2 提供端点；B1 触发 |
| B3 | HTTP（内部获取 evaluation package）+ 事件（订阅 `question.version.published`） | B2 消费 package，不 import B3 代码 |
| B4 | HTTP 之外无直接调用；B2 本地验证 B4 签发的 JWT（`iss`/`aud`/`role`/作用域） | 不 import B4 代码，不读 `b4` schema |
| MAIN | 插件装载（entry point + descriptor + health）；事件 envelope 由 MAIN 契约定义 | B2 实现行为 |
| NATS | 发布 5 个事件、订阅 1 个事件（详见 [../api/events.md](../api/events.md)） | at-least-once + outbox/inbox |

B2 **从不导入** B1/B3/B4 的代码，也**从不**直接读它们的 schema。
