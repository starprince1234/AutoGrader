# B1 工作包拆分（2~3 人并行）

与 `../../../MAIN/docs/workstreams/B1.md` 第 5 节一致。需求权威：`../../../MAIN/docs/requirements/rq-catalog.md` 第 2 节。

## 工作包

### WP-B1-A 名单与权限界面（RQ-B-1-10、RQ-B-1-20）

- 目标：名单导入向导（PDF/XLSX）、列映射交互、逐行错误表格、确认提交、名单编辑表格、并发冲突提示、角色可见性路由守卫。
- 输入：B4 名单导入与用户/课程端点契约（**只需契约已锁定**，可用契约测试夹具）。
- 产出：上述组件 + 对应测试（TC-B1-001…009）。
- 依赖：B4 端点契约冻结（不需 B4 实现完成）。
- 验收：`../requirements/acceptance.md` 中 1-10 的 5 条 + 1-20 的 4 条。
- 预估工作量：中；建议认领 1 人。

### WP-B1-B 题目与提交界面（RQ-B-1-40、RQ-B-1-50）

- 目标：富文本渲染器（安全子集）、图片加载、Monaco 编辑器、提交表单与校验、SSE 进度面板、断线重连去重、草稿本地保存。
- 输入：B3 题目端点、B2 提交与 SSE 端点契约。
- 产出：上述组件 + 测试（TC-B1-015…025）。
- 依赖：**已满足**——B3/B2 端点契约已冻结，SSE 帧类型已在 `MAIN/contracts/openapi/b2.yaml` 的 `EvaluationStreamFrame` 成文（SSE 属 HTTP 契约，不在 `asyncapi/events.yaml`）；见 `interfaces.md`。**无等待冻结项**。已知契约缺口（题目图形端点、版本题干字段）见 `interfaces.md` 的"已知契约缺口"。
- 验收：`../requirements/acceptance.md` 中 1-40 的 5 条 + 1-50 的 6 条。
- 预估工作量：大；建议认领 1 人。

### WP-B1-C 成绩、导出与日志（RQ-B-1-30、RQ-B-1-60、RQ-B-1-99）

- 目标：成绩总表与筛选、逐项证据下钻、导出对话框与下载、前端结构化日志上报。
- 输入：B2 成绩查询/导出/报告端点契约。
- 产出：上述组件 + 测试（TC-B1-010…014、TC-B1-026…034）。
- 依赖：**已满足**——`listGrades`（`GET /api/v1/grades`）与 `exportGrades`（`GET /api/v1/grades/export`）均已在 `MAIN/contracts/openapi/b2.yaml` 成文，**无等待冻结项**；证据下钻以 `../../../MAIN/contracts/schemas/evaluation-report.schema.json` 为准，不自建平行结构。缺口：契约无成绩汇总端点（见 `interfaces.md`）。
- 验收：`../requirements/acceptance.md` 中 1-30 的 5 条 + 1-60 的 5 条 + 1-99 的 4 条。
- 预估工作量：中；建议认领 1 人。

## 并行前提与串行点

- **可并行**：三条泳道在契约已锁定前提下完全可并行；三人各自持有独立路由与页面。
- **必须串行**（先做完再动别人）：
  1. 契约版本变更（走 `../../../MAIN/docs/contracts/change-protocol.md`，任何人不得自行改字段）。
  2. 共享的 API 客户端生成产物（ADR-0002，重新生成时需协调）。
  3. `interfaces.md` 的接口冻结清单更新。
  4. 路由表与角色守卫骨架（一人先建，其他人只填页面）。→ **已完成并冻结**：`src/app/router.tsx`、`src/app/routeComponents.tsx`、`src/auth/**`，登记见 `../phases/active/phase-01-business-ui.md`。
- **共享文件冲突预防**：路由注册表、API 客户端、i18n/样式变量为共享文件；改动前在 `../phases/active/` 登记 + 小步提交。

## 认领表

| 工作包 | 认领人 | 状态 | 预计完成 |
|---|---|---|---|
| WP-B1-A | （本轮由执行 Agent 实现） | 界面已实现并通过门禁；登记见 `../phases/active/phase-01-business-ui.md` | 已完成本轮范围 |
| WP-B1-B | （本轮由执行 Agent 实现） | 界面已实现并通过门禁；`RQ-B-1-40` 的两项验收因契约缺口阻塞 | 待 MAIN 判定缺口 |
| WP-B1-C | （本轮由执行 Agent 实现） | 界面已实现并通过门禁；成绩汇总仅覆盖已加载行（口径已标注） | 已完成本轮范围 |
