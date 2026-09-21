# ADR 0002 从 MAIN OpenAPI 生成客户端类型，不手抄 schema

- 状态：接受
- 背景：
  - B1 需要类型安全的 API 客户端与 SSE 事件类型。
  - 手写字段类型/请求体结构必然与 `../../../MAIN/contracts/**` 漂移，违反单一权威铁律。
  - MAIN 契约会按变更规程升级版本；手抄类型无法跟随。
- 决策：
  1. B1 的 API 类型与客户端从 `../../../MAIN/contracts/openapi/*.yaml` **生成**，生成产物入库（或纳入构建），纳入 `pnpm typecheck` 门禁。
  2. **禁止**手写契约字段类型、请求体结构、事件 payload 类型。
  3. 事件类型从 `../../../MAIN/contracts/asyncapi/events.yaml`（或统一 envelope schema）生成。
  4. 契约版本升级时，重新生成并全量 typecheck；任何手写字段类型都会被 `tsc` 报错淘汰。
- 后果：
  - 获得：类型与契约单一权威一致，契约升级可机械化跟随。
  - 获得：`RQ-B-1-40-4`（公开 DTO 无参考答案字段）在类型层可静态排除。
  - 付出：需要一条生成流程（工具选型 + 脚本 + CI/门禁接线），属于 `WP-B1` 的共享串行点（见 `../collaboration/workpackages.md`）。
  - 遗留风险：生成流程**尚未建立**（`待实现`）。**这不阻塞 B1 开工**，但要求按下列顺序推进：先生成类型 → 再写依赖契约字段的页面代码；在生成流程建立前，任何依赖契约字段的页面开发都**必须**先把生成脚本接上（属 `WP-B1` 的共享串行点，见 `../collaboration/workpackages.md`），**不得**先手写类型再补生成——那会留下最难清理的手抄类型债务。
  - **状态更新（Phase 01）**：生成流程**已建立并实跑通过**——`scripts/generate-api-types.mjs`（`openapi-typescript`）产出 `src/api/generated/{common,b2,b3,b4}.ts`；`pnpm api:generate` 生成、`pnpm api:check` 作为漂移门禁（实跑输出 "API types match MAIN/contracts."）；消费方统一经 `src/api/types.ts` 取别名，仓库内无手写契约字段类型；生成物**禁止手改**。决策本身未变，本行仅更新其落实状态。
- 关联：
  - 铁律：`../../../MAIN/docs/collaboration/module-doc-spec.md` 铁律一
  - 契约：`../../../MAIN/contracts/openapi/*.yaml`、`../../../MAIN/contracts/asyncapi/events.yaml`
  - 协作：`../collaboration/interfaces.md` 冻结清单、`../collaboration/workpackages.md` 串行点
  - 测试：`../testing/strategy.md`（契约对齐层）
