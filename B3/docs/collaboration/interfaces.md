# B3 接口约定（Interfaces）

本文件是"我依赖它什么 / 它依赖我什么"的摘要。字段级细节一律看 `../../../MAIN/contracts/**`，不在此复述。

## 与 MAIN

- 我发布 / 消费：发布 `autograder.v1.question.version.published`；订阅 `autograder.v1.evaluation.completed`（见 [../api/events.md](../api/events.md)）。
- 锁定契约版本：`0.1.0`（见 [../../contracts.lock](../../contracts.lock)）。
- 摘要漂移时：**不硬改锁**，走 `../../../MAIN/docs/contracts/change-protocol.md` 由 MAIN 重算并一次性更新四模块锁。

## 与 B1（前端）

- 我依赖它：无（B1 是纯客户端）。
- 它依赖我：公开题目 DTO、题干图片受控资源、统计查询结果。
- 失败行为：我不可用时 B1 题目列表/详情不可用，但不应崩溃（前端降级展示）。

## 与 B2（评测引擎）

- 我依赖它：去标识化 `evaluation.completed` 事件（统计输入）。
- 它依赖我：`evaluation-package`（内部接口，B2 runner 核心消费）。**这是最高风险耦合点**。
- 失败行为：package 拉取失败 → B2 该次评测进入 `failed`（不可用 0 分伪装完成）；我消费事件失败 → 统计暂缺，不阻塞 B2 评测主流程。

## 与 B4（平台）

- 我依赖它：`course_id`/`section_id` 作用域、JWT/JWKS 本地验签、NATS 运维（durable consumer / DLQ）。
- 它依赖我：无（B4 不读 B3 数据）。
- 失败行为：JWKS 不可用 → 认证请求 `401`/`503`；NATS 不可用 → readiness 反映真实状态，统计消费暂停，同步 API 路径仍可工作。

## 接口契约冻结清单

| 接口 | Owner | 当前状态 |
|---|---|---|
| `GET /api/v1/questions`、`/api/v1/question-versions`、`/api/v1/assignments`（前缀） | B3 | 契约已锁定（前缀） |
| `GET /api/v1/question-versions/{id}/evaluation-package` | B3 | 契约已锁定 |
| 题目 CRUD / `publish` / `archive` / 统计查询操作 | B3 | **已冻结**（已在 `MAIN/contracts/openapi/b3.yaml` 成文；统计端点位于已声明的 `/api/v1/questions` 前缀下） |
| `autograder.v1.question.version.published` | B3 | 契约已锁定 |
| `autograder.v1.evaluation.completed`（去标识化字段集合） | B2/MAIN | 契约已锁定 |
| `course_id` 来源与校验方式 / JWT/JWKS | B4 | 契约已锁定 |
| 公开/内部 DTO 模块划分 | B3 团队 | **已冻结 2026-09-15** → `ADR-0004`、[frozen-interfaces.md](frozen-interfaces.md) §1 |
| package 编译器接口签名 | B3 团队 | **已冻结 2026-09-15** → `ADR-0005`、[frozen-interfaces.md](frozen-interfaces.md) §2 |
| adapter manifest → package 转换接口 | B3 团队 | **已冻结 2026-09-15** → `ADR-0005`、[frozen-interfaces.md](frozen-interfaces.md) §3 |

> ⚠️ 冻结项在冻结后他人不得再改：改 ADR → 改代码 → 同步 `frozen-interfaces.md`，顺序不可颠倒；涉及 MAIN 契约的变更一律走契约变更规程（缺口记录见 `../contracts/changes/`）。
