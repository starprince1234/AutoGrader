# B4 与他模块的接口约定

## 与 MAIN：设计期 vs 运行期切分

- 依据 `../../MAIN/docs/adr/ADR-0003-api-authority-and-runtime-registry.md`：
  - **设计期（MAIN 拥有）**：`MAIN/contracts/**` 的作者身份、版本号、SemVer、变更审批、摘要门禁
    （`contracts.lock`）。
  - **运行期（B4 拥有）**：模块运行期注册表（发现/descriptor/健康聚合/能力查询），已实现并在启动时
    校验漂移。
- 我消费 MAIN 契约的**权威性**，不修改；发现不一致 → 走
  `../../MAIN/docs/contracts/change-protocol.md` 提交 CHG。
- 契约版本锁定：`contract_version` + `digest`（`../contracts.lock`，版本号本身由 MAIN 的契约流程推进，
  本模块与本文都不写死），由 MAIN 校验；摘要漂移 → 集成失败。B4 另在启动时**独立重算**同一摘要算法并与
  各模块 lock 比对，把"摘要漂移"从设计期门禁变成运行期也可观测的事实。

## 与 B1

- **B1 依赖我**：登录/刷新/退出/JWKS、用户与角色查询、课程/课序/选课、名单导入预览与确认。
- **我依赖 B1**：无（B1 是客户端）。
- **失败行为**：我不可用时 B1 无法登录/导入；B1 前端隐藏按钮不构成安全，服务端独立授权。

## 与 B2 / B3

- **B2/B3 依赖我**：我用 Ed25519 签发的 JWT + JWKS，它们**本地验签**（`iss`/`aud`/`exp`/`role`/
  课程作用域），不回调我。
- **我依赖 B2/B3**：无业务端点；只经事件（我发布 `roster.import.completed`，业务订阅为空）。
- **失败行为**：我签发令牌失败时 B2/B3 无法校验新请求；我发布事件失败（NATS 不可用）经 outbox 保留，
  不阻塞同步路径（停机演练实测 4/4 PASS）。

## 接口契约冻结清单

| 接口名 | owner | 当前状态 |
|---|---|---|
| 认证：登录/刷新/退出/JWKS | B4（我） | 冻结，`已完成` |
| 用户与角色管理 | B4 | 冻结，`已完成` |
| 课程/课序/选课 | B4 | 冻结，`已完成` |
| 名单导入（含 confirm） | B4 | 冻结，`已完成` |
| 审计查询 | B4 | 冻结，`已完成` |
| 模块注册表与健康聚合 | B4 | 冻结，`已完成` |
| 事件 `autograder.v1.roster.import.completed` | B4 | 冻结，`已完成` |
| 契约版本与摘要 | MAIN | 已锁定 |

> "冻结"指语义已约定、不得再改。**已复核**：`MAIN/contracts/openapi/b4.yaml` 覆盖 B4 声明的全部
> operation（22 个），因此**不存在"待增补的 OpenAPI operation"**。后续任何接口改动按
> `change-protocol.md` 走对应级别（P1–P3）。

## 与 MAIN 的三处遗留缺口（需 MAIN 决策，B4 不私改契约）

1. **`RosterImportPreview` 描述与属性名不一致**：描述说被拒行在 `rejected`，属性实为 `rejected_rows`。
   B4 按属性名实现；修正描述属 P3。
2. **`RosterImportPreview` 无人类可读说明字段**（`additionalProperties: false`）：导入为何需要人工映射
   只能结构化表达（`status` + `detected_columns` + `proposed_mapping`）。若前端需要直接展示原因，需 P1
   补字段。
3. **没有"给已有课程追加教师/助教"的 operation**：课程级授予目前由建课与运维命令
   `python -m autograder_b4.ops.roles grant` 产生。补 HTTP operation 需 P1。

详见 `../api/contract.md`「复核发现」。
