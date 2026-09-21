# API 与事件治理

本文件规定**接口如何被定义、谁有权改、怎么验证、怎么写错**。契约内容本身在 `MAIN/contracts/`，本文件不复制任何字段。

相关决策：`../adr/ADR-0003`（设计期 MAIN / 运行期 B4）、`../adr/ADR-0006`（事件驱动解耦）。

---

## 1. 权威性与文件布局

```text
MAIN/contracts/
├── openapi/
│   ├── common.yaml     通用约定（ProblemDetails 等）
│   ├── b2.yaml         B2 提供的端点
│   ├── b3.yaml         B3 提供的端点
│   └── b4.yaml         B4 提供的端点
├── asyncapi/
│   └── events.yaml     全部事件 channel 与 operation
├── schemas/
│   ├── language.schema.json             语言枚举（RQ-B-0-10）
│   ├── submission-envelope.schema.json  B1→B2 提交信封
│   ├── evaluation-package.schema.json   B3→B2 不可变评测包
│   ├── evaluation-report.schema.json    B2 报告（总评+细评）
│   ├── roster-import.schema.json        B4 名单导入
│   └── event-envelope.schema.json       事件统一信封
├── examples/
│   └── *.json           示例载荷（必须通过对应 schema 校验）
├── manifest.schema.json 模块清单 module.yaml 的 schema
└── CHANGELOG.md         契约变更历史
```

**规则**

1. **机器契约是唯一权威**。`docs/` 中的接口文档只写"谁提供/谁消费哪些端点与事件"，**禁止手抄字段表**。
2. 所有契约文件的 `info.version` **必须一致**（当前 `0.1.0`）。
3. **只有 MAIN 能修改** `contracts/**`（ADR-0003）。任何模块发现需要变更时走 `../contracts/change-protocol.md`。
4. 变更流程与摘要门禁见 `../contracts/change-protocol.md`；本文件只补充**设计约定**。

---

## 2. HTTP 设计约定

### 2.1 通用

| 约定 | 取值 | 说明 |
|---|---|---|
| 版本前缀 | `/api/v1/...` | 破坏性变更时提升为 `/api/v2`（**不保留 v1 兼容层**） |
| 规范 | OpenAPI 3.1 | JSON Schema 2020-12 语义 |
| 内容类型 | `application/json` | 导出端点可返回 `text/csv` / XLSX |
| 时间 | UTC RFC 3339，毫秒精度 | 见 `identifiers.md` |
| 标识 | UUID v4 | 见 `identifiers.md` |
| operationId | 小驼峰动词短语（`createSubmission`） | 供客户端代码生成使用，**不可随意改名**（会破坏生成的客户端） |
| 分页 | 游标式（`cursor` + `limit`） | 大表禁用 offset |
| 排序 | 显式 `sort` 参数 | 不依赖数据库默认顺序 |

### 2.2 错误响应（RFC 9457 Problem Details）

```text
202 / 200  成功
400        请求格式错误（无法解析）
401        未认证（无令牌/令牌无效/令牌过期）
403        已认证但无权限（角色或课程作用域不符）
404        已认证、有权限，但资源不存在
409        状态冲突（版本不可变、被引用不可删、并发编辑冲突、重复发布）
413        载荷过大（文件/总量/邮件附件超限）
422        语义校验失败（schema 通过但业务规则不通过）
429        速率限制
500        内部错误（**不含堆栈与内部路径**）
503        依赖不可用（readiness 失败的原因）
```

**硬规则**

1. `401` / `403` / `404` **不得混用**：
   - 未认证 → `401`；
   - 已认证无权限 → `403`（**不用 404 掩盖**，避免调试困难与语义混乱）；
   - 有权限但不存在 → `404`。
2. 错误响应体必须是 ProblemDetails 形状，含 `type`、`title`、`status`、`detail`、`instance`。
3. `detail` 面向人类可读，**不得**包含：堆栈、SQL、内部绝对路径、主机名、凭据、其他学生信息。
4. 认证失败不得泄露"用户是否存在"（统一错误）。

### 2.3 幂等

| 场景 | 机制 |
|---|---|
| 创建提交 | 请求体携带 `idempotency_key`；同一键重复请求返回**同一** `submission_id` 与 `202`，不产生第二次评测 |
| 确认名单导入 | 确认操作幂等；重复确认不重复写入 |
| 发布题目版本 | 版本不可变；重复发布返回 `409`（不是再发一次） |
| 邮件接收 | 以 IMAP 检查点 + `Message-ID` 去重 |

幂等键的作用域是 `(idempotency_key, 操作类型, 作用域)`，由服务端 owner 维护唯一约束。

### 2.4 认证与授权

- 访问令牌：`Authorization: Bearer <Ed25519 JWT>`。
- 签发方：B4；验证方：**每个模块本地验签**（不回调 B4）。
- 刷新凭据：仅 `HttpOnly` + `Secure` + `SameSite` cookie；**不得**出现在响应体、URL、日志。
- 授权三层 + 审计见 `../architecture/models.md` 第 9 节。
- **B1 的前端隐藏不构成授权**。

#### 2.4.1 鉴权声明写在契约里，不写在本节（CHG-0006）

本节此前**只用散文**要求 Bearer 鉴权，而契约里**没有任何 `securitySchemes`**、43 个 operation 中
42 个没有 `security` —— 于是"哪些端点需要令牌"无法被机器校验，也无人能据以生成客户端。
现在**权威在契约**：`contracts/openapi/common.yaml` 的 `components.securitySchemes` 声明
`bearerAuth`（用户会话）与 `serviceToken`（服务间），三份模块契约各自带**文档级默认**
`security: [{bearerAuth: []}]`。本节只记约定，不复制 scheme 定义。

**新增端点时的约定（这是本节的规范内容）**：

1. **默认受保护**：不要在 operation 上写 `security` 就等于要求用户令牌 —— 新写的端点默认是安全的，
   而不是默认公开、靠人记得加。这条是 42/43 缺声明的直接教训。
2. **例外必须逐条声明**，且只允许三类：凭据在请求体里（登录）、由 cookie 鉴权（刷新）、
   公钥材料（JWKS）。**"内网所以免鉴权"不构成例外**（见 §4 第 4 条）。
3. **服务间端点用 `serviceToken` 而不是复用用户令牌**：服务间调用没有"代表哪个用户"这回事。
   该值是**完整的 `Authorization` header**（含 `Bearer ` 前缀）—— 这一条曾经实际造成事故：
   集成环境把它写成裸令牌，B3 回 `401 a bearer token is required`，而 B2 的报错刻意不含 URL 与
   响应体，于是整条评测链在**取题包**阶段就断掉、表现为 `infrastructure_error`。
4. 契约里声明什么，实现就必须做什么。发现两者不一致时**不偷改任一边**：走
   `../contracts/change-protocol.md` 提交 CHG，由 MAIN 判定哪边是权威。

### 2.5 请求关联

- 入站：接受并传播 W3C `traceparent` 与 `X-Request-ID`。
- 若未提供，入口生成，并贯穿日志 → 数据库 → 事件。
- 出站（模块间）：必须携带同一 `traceparent`。

### 2.6 SSE（服务端推送）

- 用途：评测实时进度（`RQ-B-1-50`）。
- 要求：事件带**单调递增序号**供去重与排序；断线重连不重复展示历史进度。
- SSE 是**会话级**通道，不替代系统级事件（见 `events.md`）。
- 必须处理客户端断开（释放服务端资源），不得泄漏连接。

---

## 3. 端点所有权与冻结清单

| 端点前缀 | Owner | 消费者 | 状态 |
|---|---|---|---|
| `/api/v1/auth` | **B4** | B1 | 契约已锁定 |
| `/api/v1/courses` | **B4** | B1 | 契约已锁定 |
| `/api/v1/roster-imports` | **B4** | B1 | 契约已锁定 |
| `/api/v1/questions` | **B3** | B1 | 契约已锁定 |
| `/api/v1/question-versions` | **B3** | B1（公开部分）、**B2**（内部 package） | 契约已锁定 |
| `/api/v1/assignments` | **B3** | B1 | 契约已锁定 |
| `/api/v1/submissions` | **B2** | B1 | 契约已锁定 |
| `/api/v1/evaluation-runs` | **B2** | B1（SSE 进度） | 契约已锁定 |
| 成绩查询 / 导出 | **B2** | B1 | 契约已锁定 |
| 模块注册表 / 健康 | **B4**（注册表）+ host（live/ready） | 运维 | 契约已锁定 |
| `/api/v1/platform`（一致性报告） | **B5** | 任何已认证主体 | 契约已锁定（`CHG-0010`） |
| `/app`（静态站点） | **B1**（产物） | 浏览器 | 见 ADR-0007 |
| `/health/live`、`/health/ready` | **MAIN host** | 运维/编排 | 契约已锁定 |

**"契约已锁定"的含义**：路径与语义已确定，模块可以据此并行实现；**不代表已实现**（各模块当前为骨架）。

**路由冲突防护**：`module.yaml` 的 `routes` 数组在集成检查中做唯一性断言；重复路由 → 启动失败。
另有 `check_route_declarations()` 双向对账：**契约路径必须落在某个模块声明的前缀下**，且**每个声明
的前缀必须至少覆盖一条契约路径** —— 前半句防止契约承诺一个没人实现的端点，后半句防止模块占用
契约未描述的 URL 空间。注意两侧不是同一种字符串：契约写**完整路径**，`module.yaml` 写**前缀**。

**模块数量是开放的**（`ADR-0013`）：上表不是封闭清单。新增模块不需要修改 MAIN 代码，但**必须**
留下一份契约文件（`contracts/openapi/<module>.yaml`）、一份 `module.yaml` 与一份 `contracts.lock`
—— 三者都由门禁强制，`scripts/scaffold_module.py` 会生成符合规范的空骨架并打印剩余的必做步骤。

---

## 4. 内部接口的可见性规则

有些端点只应被其他模块（而非浏览器）调用，例如 B3 的 evaluation package 端点。

规则：

1. 内部端点**也必须有契约**（写在对应模块的 OpenAPI 文件中），否则无法校验。
2. 内部端点在契约中**显式标注**（描述中说明"service-to-service only"）。
3. 内部端点的响应**永不**出现在给学生的任何界面或导出中。
4. 内部端点仍然需要认证与授权（服务间凭据或内部令牌），**不允许**"因为是内网所以不鉴权"。

**当前内部端点**（以契约中的 `tags: [internal]` 为准，此处只列清单，字段定义见契约）：

| 端点 | 方向 | 凭据 |
|---|---|---|
| `GET /api/v1/question-versions/{question_version_id}/evaluation-package` | B3 → B2 | `serviceToken` |
| `POST /api/v1/question-versions/{question_version_id}/fixtures` | 上传 fixture 字节（B3） | `serviceToken` |
| `POST /api/v1/question-versions/{question_version_id}/attestations` | 记录参考解答验证存证（B3，`CHG-0008`） | `serviceToken` |

> **为什么存证端点是内部的**：它记录的是"某个**外部**校验器跑过这份内容的参考答案并观察到期望输出"。
> B3 自己不执行任何东西（`B3/AGENTS.md`），因此它只**记录并强制**；声明的摘要只被**交叉核对**
> （B3 自己重算内容摘要再比对），所以存证不可能被挪用到别的内容上。执行者与记录者分离，
> 与 `CHG-B3-0002` 的"client 声明的摘要不被信任"是同一条原则。

---

## 5. 版本与兼容策略

| 变更 | 版本 | 兼容层 |
|---|---|---|
| 新增可选字段 / 新增端点 | MINOR | **不留兼容层**（旧客户端自然忽略新字段） |
| 语义变化 / 必填性变化 | MINOR 或 PATCH（按是否破坏） | 必须全体同步 |
| 删除/重命名/改类型/改 subject | **MAJOR** | **一次性切换，绝不双轨** |

**明确禁止**（来自项目总原则）：

- 字段别名（旧名与新名同时存在）
- 双写（同时写两个字段/两张表）
- 隐藏开关（`use_new_impl=true`）
- 自动降级（新路径失败回退旧路径）
- 兼容路由（保留旧 `operationId` 或旧路径）

理由：双轨必然导致"两条路径行为不一致"且"只有一条被测到"，是隐性缺陷的温床。

---

## 6. 客户端代码生成

- B1 的 API 类型**必须**由 `MAIN/contracts/openapi/*.yaml` 生成，**不得手抄**。
- 重新生成是**共享动作**：改动后需通知 B1 全体成员（见 `../workstreams/B1.md` 的共享文件冲突预防）。
- 生成的客户端代码不得手工编辑（下次生成会覆盖）。
- 契约变更后必须重新生成并提交，否则生成物与契约漂移（属于门禁缺陷）。

---

## 7. 验证

```powershell
cd MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

当前检查覆盖：

- 契约示例可解析为 JSON；
- `contracts/schemas/*.json` 都是合法的 JSON Schema 2020-12；
- 四个 `module.yaml` 通过 `manifest.schema.json`；
- 路由与数据库 schema 无重复归属；
- 四份 `contracts.lock` 的版本与摘要等于实时摘要；
- 需求编号基线完整（32 条，含 `RQ-B-4-99`）；
- 各模块文档集合完整（文档门禁）；
- 文档 UTF-8 有效、无 BOM、无死链。

**尚未覆盖（已知缺口）**：示例载荷与其 schema 的**实例校验**（当前只校验 schema 本身合法，未验证示例通过对应 schema）；OpenAPI 与 AsyncAPI 的**结构**解析（当前只做 YAML 载入）。补齐计划见 `../testing/cases.md`。

---

## 8. 常见错误与正确做法

| 错误做法 | 为什么错 | 正确做法 |
|---|---|---|
| 在模块文档里写请求体字段表 | 必然漂移 | 只写端点名，字段看契约文件 |
| 直接改 `MAIN/contracts/**` | 破坏四方锁与门禁 | 走变更规程 |
| 为了让检查通过手填摘要 | 掩盖真实漂移 | 由 `checks.py` 计算后填入 |
| 新增字段但没更新 CHANGELOG | 无法审计契约演化 | 每次契约变更追加 CHANGELOG |
| 用 `404` 表示无权限 | 掩盖授权问题、调试困难 | 用 `403` |
| 事件里塞业务对象全量 | 泄露源码/答案/PII | 只带 ID 与去标识化字段 |
| 保留旧字段以防万一 | 双轨缺陷 | 一次性切换 |
| B2/B3 私下约定"内部接口" | 集成时无法校验 | 写进契约，标注 service-to-service |
