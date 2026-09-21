# CHG-0005 成绩端点（`listGrades` / `exportGrades`）的三处声明缺口

- 级别：**P1（增补）**——只新增"已存在行为的声明"（参数与响应码），不改语义、不删字段。
- 提出人 / 日期：B2 / 2026-09-16
- 状态：**已生效（判定：四处全部采纳，补声明）** / 判定日期 2026-09-18
- **契约版本**：0.1.0（未改契约：判定为改实现）

## 动机

`MAIN/contracts/openapi/b2.yaml` 的成绩两个 operation 有**三处"行为存在但契约没声明"**的缺口。
三处都不是风格问题：契约是集成校验与宿主"重复路由保护"的唯一依据，声明缺口会让**校验看不见**、
让其他模块按契约实现时与 B2 实际行为不一致。

### 缺口 1：`student_id` 在散文里被要求，却没有声明为参数

`listGrades` 的 `description` 原文：

> "Teachers see their authorized scope; students see only their own records. **An attempt to widen
> `student_id` is rejected with 403** rather than silently narrowed."

但 `parameters` 里**没有** `student_id`（只有 `$ref` 的 `Cursor`/`Limit` + `course_id`/`section_id`/
`question_version_id`）。B2 的实现按散文实现了它（`src/autograder_b2/api/grades_routes.py`：
`student_id` 参与过滤，学生指名他人 → 403，已由真实 HTTP 用例与主链独立探针各验一次）。

后果：按契约生成的客户端**发不出** `student_id`；反之实现接受的参数不在契约里，宿主的参数校验
无法覆盖它。`exportGrades` 的 `description` 声明 "Applies exactly the same filter semantics as
`listGrades`"，因此 `student_id` 是否适用于导出也应一并明确。

### 缺口 2：两个 operation 都没有声明 `422`

B2 对非法输入返回 `422`（`InvalidFilterError` / `InvalidCursorError` / 不支持的 `format` 映射），
但 `listGrades` 只声明 `200/401/403`，`exportGrades` 只声明 `200/401/403/404`。
契约缺 `422` ⇒ 生成的客户端会把合法的 422 当未知状态，集成测试也无法据此断言。

### 缺口 3：`/api/v1/grades` 未被 `module.yaml` 的 `routes` 覆盖

`B2/module.yaml`：`routes: [/api/v1/submissions, /api/v1/evaluation-runs]`。成绩两条路径
（`/api/v1/grades`、`/api/v1/grades/export`）**不属于任何已声明前缀**，于是：
- `b2.yaml` 开头那句 "Every path below is declared in `B2/module.yaml` `routes`" 对它们不成立；
- 宿主/registry 的"重复路由"保护（字面相等比较）看不见 B2 对 `/api/v1/grades` 的归属。
  **复核者补证**：`B4/registry.py` 的 `_assert_unique_routes` 只比对**已声明前缀**，因此成绩前缀
  当前没有任何重复归属保护。

### 缺口 4：`exportGrades` 声明了 `404`，但 B2 侧**不可能**返回它

`exportGrades` 的 `responses` 含 `404`，而 B2 **不拥有课程实体**（课程/选课属 B4）：在 `b2` schema 内
"课程不存在"与"课程存在但没有任何提交"**不可区分**（都没有行）。实现因此从不返回 `404`——
空结果返回 `200` + 仅表头文件。

**已确认事实（复核者实测）**：对**不存在的课程**发起导出，管理员得到 `200` + 5205 B 仅表头文件；
同一课程的查询得到 `200` + `{"items": []}`；覆盖该课程的教师仍然只看到自己的范围；源码里
`404`/`NotFound` 出现 **0 次**。**越权仍是 `403`**，没有用 `404` 掩盖越权。

这与 `B2/docs/api/contract.md` 第 4 节的约定"有权限但资源不存在 `404`"相抵：要么契约删掉 `404`
并写明"空结果 200 + 仅表头"，要么由 MAIN 决定 B2 是否应向 B4 查询课程存在性（跨模块调用，属新依赖）。
**倾向**（B2 + 复核者一致）：删 `404` 并在契约里写明空结果语义。

## 影响的契约文件

| 文件 | 现状 | 建议 |
|---|---|---|
| `MAIN/contracts/openapi/b2.yaml`（`listGrades`） | 散文提到 `student_id`，参数未声明；无 `422` | 加 `student_id`（query，可选，`$ref: ./common.yaml#/components/schemas/Uuid`）；加 `422` 响应 |
| `MAIN/contracts/openapi/b2.yaml`（`exportGrades`） | 无 `student_id`；无 `422`；**声明了实现不可能返回的 `404`** | 若"same filter semantics"包含 `student_id`，则同样声明；加 `422`；**删 `404`** 或写明由谁判定课程存在性 |
| `B2/module.yaml` | `routes` 未覆盖成绩路径 | 加 `/api/v1/grades`（两条路径同前缀，加一条即可覆盖） |
| `B2/docs/api/contract.md` 第 4 节 | "有权限但资源不存在 `404`"与本端点的空结果语义相抵 | 若删 `404`，同步该节的适用边界 |

> 注意：`module.yaml` 属 **B2** 的声明文件（不是 `MAIN/contracts/**`）。B2 **不自行修改**它：
> 按change-protocol 第 1 步提 CHG、由 MAIN 判定后统一改（避免"某个模块偷偷改声明"）。

## 影响的模块与具体影响

| 模块 | 影响 |
|---|---|
| **B2（提供方）** | 无代码改动（实现已按散文实现）；若 MAIN 决定给 `listGrades` 加 `student_id` 声明，B2 只需在 `docs/api/contract.md` 同步"我提供"的关系。`module.yaml.routes` 补一条后，宿主重复路由保护即可覆盖。 |
| **B1（消费方）** | 现在无法从契约生成 `student_id` 参数；补声明后可用它做"只查某个学生"的视图，并据 `422` 正确处理非法输入。 |
| **B3 / B4** | 不提供该端点；B3 的统计若走 B1 的导出，可据 `422`/`student_id` 对齐口径。 |

## 是否破坏兼容

否。三处都是"把既有行为写成声明"：不删、不改类型、不改语义。`422` 是新增响应码；
`student_id` 是新增**可选**参数；`module.yaml.routes` 是补一条已存在的前缀。

## 示例载荷变更

```diff
  parameters:
    - $ref: ./common.yaml#/components/parameters/Cursor
    - $ref: ./common.yaml#/components/parameters/Limit
    - name: course_id
      in: query
      required: true
+   - name: student_id
+     in: query
+     required: false
+     schema:
+       $ref: ./common.yaml#/components/schemas/Uuid
  responses:
    '200': ...
    '401': ...
    '403': ...
+   '422':
+     $ref: ./common.yaml#/components/responses/UnprocessableEntity
```

## 回滚方式

纯声明变更，回滚即删除新增的参数/响应码/module.yaml 那一行；不涉及数据、迁移或摘要
（若 MAIN 采纳，则按协议第 3～5 步升版本并重算四份 `contracts.lock`）。

## B2 侧的现状登记（不改契约、不掩盖缺口）

- `src/autograder_b2/api/grades_routes.py` 的模块 docstring 已如实登记缺口 1、2；
- `src/autograder_b2/plugin.py` 的 docstring 已登记缺口 3（"成绩两条路径没有被任何 `routes` 前缀覆盖"）；
- 本轮工作项的阶段证据与缺口清单见 `B2/docs/phases/active/phase-01-implementation.md`。

---

## MAIN 判定（2026-09-18）：**四处全部采纳 —— 补声明，不改实现**

**理由。** 四处都是同一个形状：**行为已经存在，契约没写**。契约是宿主路由保护与集成校验的唯一依据，
声明缺口会让校验看不见、让按契约实现的客户端与服务端行为不一致。反方向（改实现去符合缺失的声明）
在这里没有意义——删掉 `student_id` 支持会让"越权请求被静默收窄"变成现实，而那正是契约散文明确
禁止的。

**已实施的改动（`MAIN/contracts/openapi/b2.yaml`，四处）。**

| 缺口 | 改动 |
|---|---|
| 1 | `listGrades` 与 `exportGrades` 的 `parameters` 各补 `student_id`（`in: query`、`required: false`、`Uuid`）。两处都补，因为 `exportGrades` 的 description 声明"与 `listGrades` 完全相同的过滤语义"，只补一处会让两者继续不一致 |
| 2 | 两个 operation 的 `responses` 各补 `422: UnprocessableEntity` |
| 3 | `B2/module.yaml` 的 `routes` 补 `/api/v1/grades`（`plugin.py` 的 `descriptor.routes` 早已包含它；缺的只是 `module.yaml`）——本项在本次集成启动中已完成 |
| 4 | `exportGrades` 的 `responses` **删除** `404`：B2 不拥有课程实体，在 `b2` schema 内"课程不存在"与"课程存在但没有提交"不可区分，实现从不返回 404（空结果返回 `200` + 仅表头文件）。保留一个不可能出现的状态码会让生成的客户端写出永不执行的错误分支 |

**契约摘要已变** → 四份 `contracts.lock` 已由 `scripts/relock_contracts.py` 重算（不在此复制字面值）。

**模块侧改动：** B2 的实现未变（四个行为本来就已实现），只把 `grades_routes.py` 的模块 docstring 从
"与契约文本不一致的两处"改为"声明缺口已由 CHG-0005 关闭"。B1 的生成类型随之重新生成
（`api:types` 门禁在同一轮里从 `drift detected` 转回 `API types match MAIN/contracts.`），
`tsc -b` / ESLint / Vitest / `vite build` 全绿。

**验证。**

```text
MAIN:  .venv\Scripts\python.exe -m autograder_host.checks
       contracts, manifests and requirements: ok (c2bb5f4ec6b6…)
B1:    node scripts/generate-api-types.mjs --check   → API types match MAIN/contracts.
       node node_modules/typescript/bin/tsc -b       → exit 0
       node node_modules/vitest/vitest.mjs run       → 18 files / 96 tests passed
       node node_modules/vite/bin/vite.js build      → ✓ built
```
