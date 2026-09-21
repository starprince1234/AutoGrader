# B2 与邻居模块的接口约定摘要

> 契约权威在 `../../../MAIN/contracts/**`；本文件只写"我依赖它什么、它依赖我什么、失败时怎么办"与冻结清单，**不抄字段**。

## 与 MAIN

- 我发布 / 消费：发布 5 个事件、订阅 1 个事件（见 [../api/events.md](../api/events.md)）。
- 我锁定的契约版本：`0.1.0`（见 `../../contracts.lock`）。
- 摘要漂移：`contracts.lock.digest` 与 `MAIN/contracts` 实时摘要不一致时，MAIN 集成门禁失败；我不私改，走 [../../../MAIN/docs/contracts/change-protocol.md](../../../MAIN/docs/contracts/change-protocol.md)。
- 插件装载：MAIN 通过 entry point 装载，我提供 `descriptor` + `router()` + `health()`。

## 与 B3（我依赖它）

- 我依赖：`GET /api/v1/question-versions/{id}/evaluation-package`（内部，runner 核心依赖此结构）；事件 `autograder.v1.question.version.published`（缓存失效/题目可用性）。
- 失败行为：package 不可获取 → 评测置 `failed`（技术原因，记录脱敏原因），不伪造通过；事件不可消费 → readiness 反映真实状态，同步 API 路径仍可工作。

## 与 B4（我依赖它）

- 我依赖：JWT/JWKS 与课程作用域（`iss`/`aud`/`role`/作用域），本地验签。
- 失败行为：未认证 `401`；无权限/作用域不符 `403`（**不用** `404` 掩盖）。B4 不可用时不吞异常，日志与状态明确。

## 与 B1（它依赖我）

- B1 依赖我：`POST /api/v1/submissions`（提交）、SSE（进度）、报告/成绩查询、导出下载。
- 失败行为：我拒绝的请求返回明确错误码；进度事件按 `event_id`/序号去重（B1 断线重连不重复展示）。

## 接口契约冻结清单

| 接口 / 事件 | owner | 当前状态 |
|---|---|---|
| `POST /api/v1/submissions`（请求/响应语义、幂等、错误码） | B2 | 冻结 |
| `GET /api/v1/evaluation-runs/{id}` 与 `/events`（SSE） | B2 | 冻结 |
| 评测报告查询 DTO | B2 | 冻结 |
| 成绩查询与导出（筛选与列口径，须与 B1 一致） | B2 | 冻结 |
| `GET /api/v1/question-versions/{id}/evaluation-package` | B3 | 冻结 |
| `autograder.v1.question.version.published` | B3 | 冻结 |
| JWT/JWKS 与课程作用域 | B4 | 冻结 |
| 事件 envelope / 去重键 / 重试 / DLQ | MAIN（schema）+ 各模块（行为） | 冻结 |
| 内部 `SubmissionEnvelope`（归一化后） | B2-A | 已冻结（规格 [../architecture/interfaces.md](../architecture/interfaces.md) 第 2 节；代码 `autograder_b2.domain`） |
| `run_case(package, submission) -> CaseEvidence` 签名 | B2-C | 已冻结（规格 [../architecture/interfaces.md](../architecture/interfaces.md) 第 5 节；代码 `autograder_b2.domain.CaseRunner`） |

## 约定总结

- 跨模块引用只传 UUID；时间 UTC RFC 3339；错误 RFC 9457；关联 `traceparent`/`X-Request-ID`。
- 我从不 import B1/B3/B4 的代码，从不读 `b3`/`b4` schema。
