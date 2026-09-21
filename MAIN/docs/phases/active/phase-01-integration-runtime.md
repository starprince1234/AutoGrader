# Phase 01 运行时集成与端到端验证

- **状态**：**active**
- **范围**：把 Phase 00 完成条件 9（各模块静态检查与测试真实通过）与 10（PostgreSQL/NATS/host 统一启动与 readiness 验证）从"未执行"推进到**有真实命令与读数**；契约按集成需要演进并按变更规程重新锁定；B1 前端在真实组合系统上完成端到端回归。
- **归档判据**：条件 11（资源实测）与 Linux 沙箱验收仍未达成，因此本阶段与本阶段之前的工作保持 active。

> **本文件里的套件总数是"当时的"读数，不是当前值。** 它们记录的是那一刻真实跑出来的结果，因此
> **不要**把本文件里的数字改写成最新值（那会抹掉历史），也**不要**把它读成当前状态。
> **当前读数由 `MAIN/.runtime/evidence/*.json` 持有**（每个模块一个物件，绑定它被测时的契约版本与摘要），
> 由 `check_test_evidence()` 强制。`python MAIN/scripts/test_evidence.py --check` 一条命令即可读出。
> 这条说明是因为这些数字曾经在没有任何机制比对的情况下漂移了 **9 个契约版本**（见
> [phase-02 §3.32](phase-02-ai-activation-and-integration-repair.md)）。

---

## 1. 目标与完成条件

| # | 条件 | 达成 |
|---|---|---|
| 1 | `MAIN` 的 `checks.py` 三类门禁在真实环境通过 | ✅ 见 §4.1 |
| 2 | PostgreSQL + NATS + composition host 起来，`/health/ready` 逐模块点名 | ✅ 见 §4.2 |
| 3 | 契约演进（B2/B3/B4）落地并重新锁定四份 `contracts.lock` | ✅ 见 §3 |
| 4 | 通过公开 HTTP API 播种出一套可演示的数据（不直接写 SQL） | ✅ 见 §4.3 |
| 5 | 四个模块的测试套件在**隔离实例**上真实跑通 | ✅ 见 §4.4 |
| 6 | B1 在真实组合系统上端到端可用 | ✅ 见 `B1/docs/phases/active/phase-02-apple-redesign-and-e2e.md` |
| 7 | 容器交付路径：空卷 `docker compose up` 可用（审计项 E1） | ✅ 见 §13 |
| 8 | Linux 沙箱与资源实测 | ❌ **未执行**（本环境无 nsjail / cgroup v2），见 §6 |

---

## 2. 实际变更（MAIN 侧）

### 2.1 composition host

| 文件 | 变更 | 原因 |
|---|---|---|
| `host/src/autograder_host/main.py` | 新增 `module_settings(environ, module)`：`<MODULE>_` 前缀变量**覆盖**同名通用变量，而不是填补空缺 | 原实现用 `setdefault`，于是全局 `DATABASE_URL`（平台超级用户）对每个模块都可见，B4 的最小权限守卫**正确地**拒绝启动。守卫没错，解析错了 |
| 同上 | 每个模块拿到自己的 `HostContext`（`app.state.contexts`），装载完路由后调用可选的 `configure_app(app, ctx)` | 模块的领域异常在宿主里会变成 500（`GET /api/v1/questions` 无令牌返回 500 而非 401）；`APIRouter` 装不了异常处理器，只有应用能 |
| 同上 | 静态站点回退：`SpaStaticFiles` 捕获 404 并返回 `index.html` | `/app/login` 深链 404。**注意**：Starlette 是 `raise` 而不是 `return 404`，因此必须捕获 `StarletteHTTPException` |
| `sdk/src/autograder_platform/protocol.py` | 协议里声明可选的 `PlatformModule.configure_app(app, context)` | 让钩子可被发现；宿主用 `getattr`，缺少它的模块照样装载 |

### 2.2 契约

| 文件 | 变更 |
|---|---|
| `contracts/openapi/b2.yaml` | `SubmissionTransitions`（`rejected` 可从 `received`/`validated`/`running` 到达）、`EvaluationStreamFrame`、`SubmissionView.evaluation_run_id`（可空）、grades 的 `student_id` 参数 |
| `contracts/openapi/b3.yaml` | `QuestionVersionSummary` + `QuestionPublic.versions`；`QuestionVersionPublic.stem_markdown`（必需）与 `stem_assets`。**并修掉一处重复的 `additionalProperties` 键**——`js-yaml` 会拒绝重复映射键，而 PyYAML 默默接受 |
| `contracts/openapi/b4.yaml` | `UserSummary.username`（必需）、会话与刷新响应补 `username`/`roles`/`scope` |
| 四份 `contracts.lock` | 由新脚本重算写入（**绝不手填**） |

摘要演进：`de8f6834…` → `615e63d6…` → `93574afb…` → `c92d1070…` → `ef108766…` → **`36a0209f0389aeeec305cd586994c0396e6b089cd64f4276f66e815f0a64f93b`**（当前）。

### 2.3 新增脚本与运行配置（`MAIN/scripts/`、`infra/`）

| 文件 | 作用 |
|---|---|
| `scripts/relock_contracts.py` | 重算契约摘要并写入四份锁（变更规程第 5 步）；`--check` 为只读模式。此前这一步是手工的，而手工正是"摘要必须由工具计算"这条硬规则要防的事 |
| `scripts/seed_integration.py` | 只走**公开 HTTP API** 播种：管理员登录 → 建教师/学生 → 教师建课程（创建者因此获得课程作用域）→ 建课序 → 上传中文表头 XLSX 名单 → 列映射 → 确认 → 建题/发布/建作业；对 409 幂等容忍 |
| `scripts/run_host.ps1` | 载入 `.env.integration` 并用 uvicorn 起 host（端口 18000） |
| `scripts/verify_question_bank.py` | 题库真实性门：真实执行每份参考答案，按题目自己声明的比较策略比对期望输出。比较逻辑与子进程环境是 `B2` 判题机的**忠实移植**（逐行 `rstrip` + `SANDBOX_ENV_FIXED`），见 §4.6 |
| `scripts/import_question_bank.py` | 题库导入门：只走公开 HTTP API，逐题 `create →（上传 fixture）→ publish`，并按文件分组各建一个 `assignment` |
| `seed/question-bank/*.json` | **100 道已验证题目**（+4 道校验器自检题），6 个主题分片；题面原创，含 377 个用例与 13 个 `file` 类 fixture。见该目录的 `README.md` |
| `.env.integration`（gitignored） | 各模块 DSN、`B4_JWKS_URL`、`B2_B3_BASE_URL`、共享服务令牌、`JWT_PRIVATE_KEY_FILE`、`B4_MODULE_ROOT`、`B1_DIST_DIR` |
| `infra/postgres/init.sql` | **重写**：原先只建三个 schema，没有任何登录角色，因而没有任何模块能连接平台库。现在建 `b2/b3/b4` schema 与四个最小权限角色（`NOSUPERUSER`、`CONNECTION LIMIT`），并按 schema 授权；表级授权留给各模块迁移或 `ops.provision` |
| `scripts/probe_host_assembly.py` | **重启就绪探测**：完整走一遍 `create_app()`（模块发现与按能力定序、settings view、路由注册、静态挂载）但**不绑端口**，因此可在 host 继续对外服务时安全运行。它导入的是**已安装**的包，也就是真实重启会加载的那一份，所以"改了源码没重装"必然在这里失败——这正是它要拦的情况。退出码 0/1，见 §3.6、§12.1 |
| `scripts/event_chain_status.py` | **整条事件链的单条读数**：按 `information_schema` *发现*各模块 outbox/inbox 表及其列（不硬编码表名列名，也不复制模块 DDL；缺列报 `n/a` 而不猜），再读 JetStream 各 stream 的 `messages`/`bytes`。`attempts == 0` 与 `last_error` 同时为空 = 中继从未运行过，这与"投递失败"是两种不同故障，只有这个组合能区分，见 §12.3.1 |
| `scripts/gate_status.py` | **逐门禁 PASS/FAIL**。`checks.py` 刻意 fail-fast（门禁之间有依赖，后面的失败叠在前面的失败上是噪声），但开发期间这会让**第一个红门禁藏掉它后面的全部门禁**，于是"门禁红了"这句话不携带任何信息量。本脚本用 `ast` 读 `main()` 自己的源码取得调用顺序，因而**往 `main()` 里加门禁不会让这个列表过期**，见 §12.6 |

### 2.4 门禁新增（`host/src/autograder_host/checks.py`）

| 门禁 | 作用 |
|---|---|
| `check_contract_parses_strictly()` | 用**严格** YAML 加载器（拒绝重复映射键）解析契约。PyYAML 默认接受重复键，而 B1 的 `openapi-typescript` 链路用 `js-yaml` 会拒绝——同一次提交在两个工具里一个通过一个失败 |
| `check_module_governance()` | 四个模块的必需文档集合与阶段记录 |
| `check_document_encoding_and_links()` | UTF-8 / 无 BOM / 无死链 |
| `check_local_configuration_consistency()` | 迁移 head 与 `module.yaml` 一致；`.env.example`/`alembic.ini` 的端口与 `compose.yaml` 一致 |
| `check_contract_examples()`（强化） | 示例必须通过**归属 schema** 的实例校验，而不仅是合法 JSON |
| `check_deprecated_b3_artifacts()`（强化） | `.pyc` 不再被跳过，按字节搜索被移除设计的标识；追踪矩阵只拦截"需求声明"式出现 |

---

## 3. 集成过程中发现并修复的真实缺陷

| # | 缺陷 | 现象 | 处置 |
|---|---|---|---|
| I-01 | host 的 `module_settings` 用 `setdefault` | B4 `ConfigurationError: refusing to start: database role 'autograder_platform' is over-privileged` | 改为覆盖语义（§2.1）；守卫本身是正确的 |
| I-02 | B3 的令牌验证器写死 RS256 | B4 用 Ed25519 签发（ADR-0006），于是**所有**令牌校验失败 | `auth.py` 改为 EdDSA（JWKS `kty: OKP`/`crv: Ed25519`）；`role` 声明按 B4 的**列表**解析 |
| I-03 | B3 的 JWKS 地址取自 `B3_JWKS_URL` | 宿主注入的是 `B4_JWKS_URL`（提供方命名），B3 得到 `token_verifier=None`，全部 401 | B3 读 `B4_JWKS_URL`；不自造别名 |
| I-04 | 模块的领域异常在宿主里变成 500 | 未认证的 `GET /api/v1/questions` 返回 500 而不是 401 | SDK 的 `configure_app` 钩子（§2.1） |
| I-05 | `/app/login` 返回 404 | SPA 深链全部失效 | 宿主静态站点回退（§2.1） |
| I-06 | 构建后的前端请求 `/assets/...` 而宿主只服务 `/app/...` | 页面加载出空壳，所有资源 404 | Vite `base: "/app/"` + TanStack Router `basepath: "/app"` |
| I-07 | `b3.yaml` 有重复的 `additionalProperties` 键 | `js-yaml` 拒绝解析 → B1 的类型生成失败；PyYAML 侧无感 | 删除重复键；新增严格解析门禁（§2.4），使同类问题在 MAIN 侧先失败 |
| I-08 | `b4.ops.roles` 的日志键用了 `created` | `Attempt to overwrite 'created' in LogRecord` —— `created` 是保留的 `LogRecord` 属性，CLI 直接崩 | 改名为 `grant_created` |
| I-09 | 播种把课程建在**管理员**名下 | 教师随后 `POST /api/v1/questions` 恒 403"课程不在调用者的授权范围内"；失败信息里没有任何线索指向缺失的课程授予 | 播种植种改为**教师建课程**（契约里 `createCourse` 会为创建者写入课程级 `teacher` 授予）；课程已存在时显式校验教师能否看到它，并打印可直接执行的运维授予命令 |
| I-10 | composed 系统里**没有评测 worker** | `EvaluationOrchestrator.run()` 是同步的，但没有任何东西驱动它：提交后运行永远停在 `validated` | 新增 `B2/src/autograder_b2/worker.py`（`--once` / `--idle-exit`），按设计**单 worker**（无租约/认领表），在 `docs` 中如实记为单实例 |
| I-11 | B1 用 `submissions.state` 判断何时能订阅进度 | 运行行在受理**之后**创建，而状态机里 `failed` 只能从 `running` 到达，因此"受理后、运行前"的基础设施故障让提交停在 `validated`，客户端没有任何可订阅的东西 | 契约给 `SubmissionView` 加可空的 `evaluation_run_id`；B1 轮询该字段直到出现 |
| I-12 | 没有创建首个管理员的路径 | 空库 → 没有任何管理员 → `POST /api/v1/users` 需要管理员 → 死锁 | B4 新增 `ops/bootstrap_admin.py`（幂等、Argon2id、口令不回显） |
| I-13 | **B4 的测试套件会在共享平台库上逐表 `TRUNCATE`** | 一次 `uv run pytest` 清空集成环境的 `b4.users` / `roles` / `sections` / `enrollments` / `credentials` / `roster_imports`；现象与"种子没跑过"完全一样 | 每个后端模块获得**模块本地测试实例**（见 §4.4）；B4 的 conftest 在 import 期把 DSN 钉到本地实例 |
| I-14 | B2 的测试实例只存在于"某次 `docker run` 的记忆里" | 文档写着 55433/54223，但没有任何文件定义它们，容器一没套件就大面积报错 | 新增 `B2/compose.yaml`（55433 + 54223）与 `B4/compose.yaml`（55435 + 54224），端口沿用各模块文档已使用多轮的编号 |
| I-15…I-17 | 题库填充时暴露的三个问题（列表静默截断、校验器比判题机更严、校验器不镜像沙箱编码） | 见 §4.6 | 三处均已修复并有测试/实测证据 |

---

## 4. 验证记录（真实命令与读数）

### 4.1 MAIN 门禁

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

```text
contracts, manifests and requirements: ok (36a0209f0389aeeec305cd586994c0396e6b089cd64f4276f66e815f0a64f93b)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok
```

### 4.2 启动与 readiness

```powershell
docker compose -f MAIN/compose.yaml up -d          # main-postgres-1 (55432) + main-nats-1 (54222)
MAIN\scripts\run_host.ps1 -Port 18000              # uvicorn，B1 dist 由 /app 提供
```

```json
{"ready": true,
 "modules": [{"name":"B4","healthy":true,"detail":"postgres: postgres reachable; migration head 20260915_0002 matches module.yaml; nats: nats is reachable at 127.0.0.1:54222"},
             {"name":"B3","healthy":true,"detail":"question bank ready; token_verifier=JwksTokenVerifier publisher=NatsEventPublisher service_token_configured=True"},
             {"name":"B2","healthy":true,"detail":"http-intake: ok …; evaluation-chain: ok …（取包端点、工作区根与库结构版本均已就绪）; message-broker: ok …（未发布 4 条，DLQ 0 条）"}]}
```

三个模块的 `descriptor.requires` 全部满足，路由前缀无冲突，装载顺序由 capability DAG 决定。

### 4.3 播种（只走公开 HTTP API）

```powershell
MAIN\.venv\Scripts\python.exe MAIN\scripts\seed_integration.py --base-url http://127.0.0.1:18000
```

```text
seeding http://127.0.0.1:18000
  admin login ok
  teacher=6c2378d0-1c8a-4abc-bd1a-5744cc40ea87
  student=fd29cd1d-9ec5-43f5-89a9-eca6f822e96d
  course=8cf8259c-6e95-4aee-92c0-ee601d84b2a0
  section=108fc701-a136-43f0-8cb7-f3eeb266a589
  roster-import=a6824508-8890-4d79-b2fb-d34b8345aeba status=previewed
  roster confirmed
  question=7e095558-d8c2-4fac-a058-36157b3c7d77
  version=53f8ac1a-90ea-4083-8591-0ff1e583354d
  published={"version": 1, "status": "published", …}
  assignment={"assignment_id": "8e9cb294-21b8-4601-9156-f85191b9ad18", …}
wrote MAIN\.runtime\seed\seed-summary.json
```

播种全程使用真实 HTTP，因此它同时是 B4（身份/课程/名单导入）、B3（题库/发布/作业）与认证链的一次端到端检查；它无法造出产品自己造不出的状态。

### 4.4 四个模块的测试套件（隔离实例）

| 模块 | 实例 | 命令前置 | 读数 |
|---|---|---|---|
| B1 | — | — | `18 files / 96 tests passed`；`tsc -b` 0；`eslint --max-warnings 0` 0；`api:check` "API types match MAIN/contracts."；`vite build` ✓ |
| B2 | PostgreSQL **55433** + NATS **54223**（`B2/compose.yaml`） | `B2_TEST_DATABASE_URL`（需 `CREATEDB`，套件为每个测试模块建临时库）、`B2_TEST_NATS_URL` | `1134 passed, 9 skipped, 1 xfailed`，exit 0 |
| B3 | PostgreSQL **55434**（`B3/compose.yaml`） | 默认值即隔离实例（`B3_TEST_*_URL` 可覆盖） | `282 passed` |
| B4 | PostgreSQL **55435** + NATS **54224**（`B4/compose.yaml`） | 无（conftest 在 import 期钉住本地实例） | `209 passed`，exit 0 |

> 这些都是**修完后**的读数。修之前 B2 是 `440 errors`、B4 是 `117 errors`（都因为被指向了集成库），
> B3 是 `4 failed`（契约演进后未跟上的断言）。逐条原因见
> `B2/docs/phases/active/phase-02-test-instance-and-suite.md`、
> `B3/docs/phases/active/phase-02-contract-alignment-and-suite.md`、
> `B4/docs/phases/active/phase-02-test-instance-isolation.md`。

### 4.5 端到端（Playwright MCP）

完整逐步记录见 `B1/docs/phases/active/phase-02-apple-redesign-and-e2e.md`。摘要：

登录（教师/学生/管理员）→ 按角色过滤的导航 → 题目列表 → 题目详情（版本下拉、按版本渲染冻结题干）
→ 作答页（Monaco）→ 提交（`submission_id = 7dbc0642-…`，55 B）→ SSE 终态
`已结束 / 阶段 terminal / sequence 3` + `评测技术失败（与代码质量无关）· 基础设施错误`
→ 成绩页（1 行、状态"技术失败"、得分"未完成"而非 0 分、分布图）→ 名单（中文表头）
→ 名单导入 → 用户与角色（管理员）。深浅两种配色均已核对。

### 4.6 题库填充（100 道题）

平台此前只有 1 道种子题，"有内容可用"这一条不成立。本轮为一个课程题集建立了**两道门**：
先证明真实性，再导入。

```powershell
cd MAIN
.\.venv\Scripts\python.exe scripts\verify_question_bank.py     # 104/104 passed
.\.venv\Scripts\python.exe scripts\import_question_bank.py --course-id … --section-id …
```

```text
published 12 question versions     # 02-file-io（file adapter + 13 fixtures）
published 18 question versions     # 01-text-tools
published 16 question versions     # 03-process-scheduling
published 18 question versions     # 04-data-structures
published 18 question versions     # 05-encoding-parsing
published 18 question versions     # 06-shell
```

数据库实测（`b3` schema）：`questions=106`、`published versions=103`、`test_cases=377`、
`fixtures=16`、`assignments=8`、`assignment_items=102`、`reference_answers=106`。
浏览器实测：题目列表首屏 100 行 + 「加载更多」，点击后 103 行、标题变为「已加载 103 道」、
按钮消失并显示「已到末页」。

**这一步暴露了三个只在"内容真的多起来"时才显形的问题**，都已修复并留档：

| # | 问题 | 处置 |
|---|---|---|
| I-15 | 题目列表只取一页且不给"还有更多"的信号，超过 100 道之后**静默消失** | B1 的 `useQuestionList` 改为按 `next_cursor` 的 `useInfiniteQuery` + 「加载更多」/「已到末页」，并新增 4 个固定翻页行为的测试 |
| I-16 | 校验器的 `ignore_trailing_whitespace` 只对整串 `rstrip`，比判题机更严 → 幻影失败，出题者差点据此放宽比较策略 | 改为 `B2/.../comparator.py` 的忠实移植（逐行 `rstrip`）；`numeric_tolerance` 也改为逐行逐 token |
| I-17 | 校验器不镜像沙箱环境。本机 Python 是 **GBK**，`len("中文输入")` 在沙箱里是 **4**、在 GBK 下是 **7** | 子进程环境镜像 `SANDBOX_ENV_FIXED`（`LANG`/`LC_ALL=C.UTF-8`、`PYTHONIOENCODING=utf-8`）。不修这一条，含非 ASCII 的题目会"本地通过、判题机上不一致"——**参考答案与自己的期望输出对不上** |

另外两处只在真实交互里才能发现的约束，写进题库 README 以免后人重踩：
`file` 类题目的 fixture 摘要必须在**创建时**声明（那时还没有 version id 可上传，服务端在 publish
时交叉校验）；本地校验 shell 经 `bash.exe`（WSL）调用，其互操作层会重新解析命令行，因此
`args` 里不能出现 `|`/`;`/`&`/`$`（Linux 判题机直接传 argv，没有这个限制）。

题面为原创编写，未复制教材/OJ 题面；隐藏用例输入与期望输出不出现在题面中（逐题机检）。

---

## 5. 与各模块的契约关系

- 本阶段对 `MAIN/contracts/**` 的改动**全部由 MAIN 作出**，并按 `contracts/change-protocol.md`
  重新锁定四份 `contracts.lock`；模块侧只做实现与断言对齐，未修改契约或锁。
- 仍未判定（需要书面裁决）的 CHG：`MAIN` CHG-0001…0005（事件 `event_id` 规则取 v4、inbox 复合主键
  `(consumer, event_id)`、逐事件 payload 必需键、`causation_id` 必填、grades 声明）与
  `CHG-B3-0001/0002/0003`（文件比较粒度＝文本 diff **不在范围**、资源/fixture 端点采纳、统计措辞
  中的"独立学生数"归 B2）。这些**不阻塞**当前运行，但仍是 §6 的遗留项。
- B2 的 `student_number` / `display_name` **恒为 null** 是**架构决定**（B2 只拥有 `b2`，不读 `b4`），
  不是缺陷；正确补全位置是 B4 提供批量查询或网关层补全。B1 已相应把该列表头改为"学生"以避免
  给数据贴错标签。

---

## 6. 未执行（必须如实记录）

| 检查 | 未执行原因 | 恢复验证所需 |
|---|---|---|
| Linux 安全门禁（SEC-01…20）与资源门禁（RES-01…05） | 本环境是 Windows，无 nsjail / cgroup v2；`B2/verify/linux-nsjail/` 的镜像已构建，但未跑完整验收 | Linux + 特权容器；见 `../../runbooks/linux-acceptance.md` |
| 真实 `completed` + 分数路径 | 依赖上一行：沙箱阶段在 Windows 上如实返回 `infrastructure_error`（ADR-0004、SEC-20 **禁止**降级为裸进程） | 同上 |
| 资源实测（Phase 00 条件 11） | 同上 | 同上 |
| B1 `pnpm` 脚本（`pnpm lint/test/build`） | 本环境 `pnpm` 经 DSH runner 用管道 stdio 启动，Node 的 `child_process` 管道被沙箱拒绝（`spawn EPERM`） | 直接调用 `node_modules/.bin` 下的等价命令（本轮已如此执行，逐条对应） |

**明确声明**：以上未执行项**不得**被表述为已通过。

---

## 7. 遗留项

| 遗留项 | 影响 | 责任 |
|---|---|---|
| CHG-0001…0005、CHG-B3-0001…0003 未书面裁决 | 事件与统计的部分语义仍靠约定 | MAIN |
| `ModuleDescriptor.events` 不区分 publishes/subscribes | 扁平元组承载两个方向（B2/B3 已在注释中写明）；B4 只发布所以无差别 | MAIN（SDK），P3 级变更 |
| B2 grades 无姓名/学号 | 成绩页只能显示用户 ID | B4 提供批量查询，或网关层补全 |
| 契约缺口：题目资源无读取端点；若干 200 响应无 schema；成绩无汇总端点；无"当前用户资料"端点 | B1 逐条降级并如实说明 | MAIN（契约） |
| 播种脚本依赖运维命令补课程授予（课程已存在且无授予时） | 从空库播种是一次命令，从"半播种"状态恢复需要一条运维命令 | MAIN（脚本已打印确切命令） |
| 三个模块的测试实例需要手工 `docker compose up` | 首次跑套件前多一步 | 各模块（已写进 runbook） |
| `E16`：迁移 DDL 标准过泛——复合自然键、内容寻址主键、outbox/inbox 表名三类被误判违规（详见 §12.2） | 另有 57 条真违规；违规数不能作为完成条件 | B4（标准与检查器），B2/B3（各自迁移） |

---

## 8. 契约 0.1.0 → 0.4.0 与本轮门禁加固（2026-09-18，第二轮）

### 8.1 契约变更（四份 CHG）

| CHG | 内容 | 版本 |
|---|---|---|
| `CHG-0006` | 补全鉴权声明：新增 `securitySchemes.bearerAuth`/`serviceToken`，三份 OpenAPI 加**文档级默认** `security` + 逐条例外（`createSession`/`refreshSession`/`getJwks` 公开，B3 两个内部端点用 `serviceToken`）。此前 **43 个 operation 中 42 个没有 `security`** | 0.2.0 |
| `CHG-0007` | 补全响应 schema：`listAssignments` 新增 `AssignmentPublic`（**契约此前从未描述过"作业"长什么样，而平台已建 8 个作业**）；`listEnrollments` 新增 `EnrollmentPublic` 与 **`student_id`（可空）** | 0.2.0 |
| `CHG-0008` | B3 写路径补全：`QuestionDraftInput.tags`（**读路径声明了一个结构上不可能产生的值**，106 道题标签全空）、`as_new_version` 参数（**契约 summary 与实现描述相反**）、`publish` 新增**参考解答验证存证**前置 | 0.3.0 |
| `CHG-0009` | 模块名 pattern 由 `^B[1-4]$` 放宽为 `^B[0-9]{1,3}$`（ADR-0013） | 0.4.0 |

### 8.2 四份 ADR（D/E/F 的裁决，取代之前的"待决"）

| ADR | 结论 |
|---|---|
| `ADR-0010` | 沙箱机制**维持 nsjail**（复评：Docker 需常驻 dockerd ≈100–200 MB，512 MB 账本下不可行且隔离强度不更高），关闭 ADR-0004 待确认项；实质改成**让隔离可证明**：金丝雀自证、逐用例资源实测、**违规必须落 `rejected`** |
| `ADR-0011` | 跨 schema **物理隔离保留**（与"最小权限保持不动"一致），跨模块组合改走**声明式 API + 关联键入契约**（`EnrollmentPublic.student_id` 即落地） |
| `ADR-0012` | 职责迁移为**提议**：准备到"课程方点头即可执行"，但**未生效**（改它需要课程方认可，否则按 XLSX 判两条需求同时未完成） |
| `ADR-0013` | 模块数量开放（任意 N 个），并给出三处硬编码封堵点的处置 |

### 8.3 本轮新增的四条门禁（都验证过"会失败"）

| 门禁 | 抓到的真实缺陷 |
|---|---|
| `requirement_status_drift()` + `scripts/sync_requirement_status.py` | MAIN 全局矩阵**32 行里 31 行**仍写 `骨架`/`待实现` 而四个模块都写 `已完成`。状态列改为**从拥有者模块生成**；篡改一行 → 门禁 exit 1 并指名 |
| `check_decision_references()` | `ADR-0008` 曾被 4 处引用而**文件不存在**；`rq-catalog.md` 指向存在于 **B3 仓库**的 CHG 路径。注入一个不存在的 ADR 编号与一个不存在的 CHG 编号 → exit 1 |
| `check_route_declarations()` | 契约路径（完整路径）与 `module.yaml`（**前缀**）此前从未对账。按前缀语义实现后一致 |
| `check_module_descriptors()` | 本轮**我自己造出来的**漂移：改了四份 `module.yaml` 与四份锁，但三个模块的**运行期描述符**仍写旧版本 → host 无法启动（B4 注册表报 `descriptor.contract_version='0.1.0'` 而 manifest 是 `0.4.0`）。该检查**调用模块声明的工厂**取真实描述符，不靠正则猜 |

同时修掉一个**工具+门禁联合漏洞**：`relock_contracts.py` 只重写 `digest:` 行、不碰 `contract_version`，
而 `check_manifests()` 只把**两个手工副本**（锁 vs manifest）互相比对、**从不与契约比对**——于是
版本 0.1.0 与契约 0.2.0 并存且门禁全绿。现在版本由 `declared_contract_version()` 从
`info.version` 读取并三处强制一致，工具与门禁共用同一实现。

### 8.4 即插即用的打包层修正（ADR-0013 / CHG-0009）

`MAIN/pyproject.toml` 此前把 `autograder-b2/b3/b4` 声明为**核心依赖**，于是"不改 MAIN 即可装载"
在**打包层**是假的：没有那三个目录，`uv sync` 连 MAIN 自己的 venv 都建不出来。现在核心依赖只含
SDK 与运行时，业务模块进 `modules` 可选依赖组，`uv.lock` 已重新生成（`uv lock`，实测 exit 0）。
MAIN 侧所有 `uv sync` 指令已同步为 `--extra modules`（模块自身的 sync 未改）。

`host` 侧另修两处：同名 entry point 由"**两处口径相反**的静默取舍"（host 后者胜、B4 注册表前者胜）
改为**响亮失败**；`B1_DIST_DIR` 由 CWD 相对改为**锚定 MAIN 根**，且挂载失败时**发出明确告警**
（此前 `index.html` 缺失会**静默不挂载**，`/app` 404 而 API 看起来健康）。

### 8.5 本轮实测读数

```text
contracts, manifests and requirements: ok (1568a98746c8363e692091dd7a21b4a9bc8b6d5e243a0820c907930bf00d4c22)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok
MAIN host tests: 1 passed
host._load_modules() → ['B4', 'B3', 'B2']（capability DAG 顺序；B4 注册表 reports manifests ["B1","B2","B3","B4"]）
uv lock → exit 0；锁内 MAIN 依赖 = [autograder-platform, fastapi, nats-py, psycopg, pydantic, sqlalchemy, uvicorn]
```

### 8.6 本轮未完成（不得表述为已通过）

- **合成第三方模块与即插即用一致性套件（A1）尚未交付**：ADR-0013 的 pattern 与打包层已就位，
  但"新增一个模块后它同时被装载与被治理"这条**未被验证**。
- **容器部署路径（E1）**正在修复中，验证须在独立 compose project 与不同端口上做，**结果未出**。
- **`CHG-0007` 的 `student_id` 尚未由 B4 实现**：B1 的「谁还没交」代码完成但端到端仍显示"未绑定账号"。
- **另有 8 个 operation 仍无响应 schema**（已在 `CHG-0007` §7 逐条登记）。
- **0.3.0/0.4.0 新增字段的线上 schema 校验未重跑**（0.2.0 时为 4/4）。
- 运行中的 host 是**旧代码**（job 在契约 0.2.0 时启动）；待模块代理结束后需重启并重跑浏览器 E2E。

---

## 9. 事件链路：从"没人发"到"没有流"（第三轮，2026-09-18）

### 9.1 一个平台级缺口，只有 B4 能关

B2 修好了它的 outbox 投递（`worker.run_once` 此前**同步调用**异步的 `dispatch`，协程被创建后丢弃
—— 就是本项目早先观察到的 `coroutine 'NatsOutboxDispatcher.dispatch' was never awaited`）。
修好后平台实测的行为变化很说明问题：

```text
修前：15 行 / published 0 / attempts 0
修后：15 行 / published 0 / attempts 1，每行末次错误为
      EventBusUnavailable: publish failed ... NoStreamResponseError: nats: no response from stream
```

只读核对运行中的 broker（`docker exec main-nats-1 wget -qO- "http://127.0.0.1:8222/jsz?streams=true"`）：
现存 stream 只有 **`ROSTER_IMPORT`** 与 **`B4_DLQ`**，**没有任何匹配 `autograder.v1.>` 的 stream**。

因此**故障原因从"没有人发"变成了"没有地方可发"**：`submission.accepted`、`evaluation.*`、
`question.version.published` 一个都发不出去。B2 按所有权没有自行创建 stream（正确）。

**这不是一个投递失败，而是整条 `RQ-B-3-30` 链路**：B3 的统计消费 `evaluation.completed`，而 B3 实测
`event_inbox = 0` / `question_version_stats = 0`。此前我记的是"该链路从未消费过真实事件"，现在成因
精确了：**根本没有流可消费**。ADR-0006 的事件解耦是**设计完成、接线未完成**。

处置责任：B4（`messaging/jetstream.py` 的 `STREAMS` + `ops.messaging provision`），并且必须**重算
0.25 GB 账本的划分**（此前 domain stream 被设为"账本 − DLQ"是假定它是唯一消费者）。已通知 B4。

### 9.2 一条必须遵守的操作约束

**在 stream 建好之前，不得对平台反复运行 worker。** B2 实测每跑一遍 `attempts += 1`，达到投递上限
即进入 DLQ——连跑五遍会把积压的 15 行全部死信。只能用 `--once` 有意识地跑，若有行落入 DLQ 需
replay 恢复。当前实测 `max_attempts = 1`、`would_deadletter = 0`，未造成损失。

### 9.3 本轮已交付（模块侧，均有变异验证）

| 模块 | 交付 | 关键读数 |
|---|---|---|
| B2 | E9 运行期安全违规改为 `rejected`（不再给分）；outbox 真实投递；E10 可配置保留期 | `ruff` 全过；**1145 passed / 9 skipped / 1 xfailed**；两处变异验证（去掉升级 → 测试失败） |
| B1 | C1 作业中心（学生「我的作业」+ 教师「提交情况」，与「谁还没交」共用同一个 `buildCohort` 连接）；账号列表分页；死代码清理 | `tsc`/`eslint`/`api:check`/`build` 全绿；**23 文件 / 133 用例** |

B1 的一个防御性设计值得记录：`useLoadAllPages` 给自动翻页设了 **40 页（8000 行）上限**，
游标异常不再把浏览器拖进无限请求；到上限按"未加载完"处理（**不判定**），而不是假装完整。

B2 在实现保留期时抓到一个很隐蔽的陷阱：文件 mtime 是微秒精度而 `utc_now()` 截断到毫秒，
于是刚写入的游离文件看起来比本轮开始时间还新（6 个探针里 5 个），默认窗口会**静默变成"永不回收"**。

### 9.4 一个必须记住的陷阱：门禁可能验证的是"已安装副本"而不是工作树

为证明新增的"摘要实现一致性"门禁会失败，我改了 `B4/src/.../registry.py` —— **门禁没报错**。
根因不是探针写错：`autograder_b4.registry` 解析到 **site-packages 里的已安装副本**，不是工作树。
危害比一个检查大得多：**只要安装副本是旧的，所有导入模块代码的门禁都在验证旧字节，而评审读的是新源码。**

已新增 `check_module_installations()` 逐包比对工作树与已安装副本的 sha256。它当场抓出 B4 的
`registry.py` 已过期（B4 代理改了源码未重装 —— 意味着**它当时跑的测试验的是旧代码**），已修复。
该检查自身也有两处缺陷并已修正：它一度把 B1 的 TypeScript `src/` 子目录当 Python 包，
甚至匹配到**标准库的 `logging`**，报出"logging 副本过期"的胡话。

---

## 10. 契约 0.5.0 → 0.7.0、证据台账与文档合规（第四至七轮）

### 10.1 三份 CHG

| CHG | 内容 | 版本 |
|---|---|---|
| `CHG-0009` | 模块名 pattern 由 `^B[1-4]$` 放宽为 `^B[0-9]{1,3}$`（ADR-0013）。**首轮只改了一处**，`b4.yaml` 的两处是 B4 实现时发现的：第五个模块能过 manifest 校验、却在注册表响应里违反 OpenAPI 声明 | 0.4.0 |
| `CHG-0010` | 新增 **B5 平台一致性审计模块**（`GET /api/v1/platform/conformance`）。B5 是 ADR-0013 的验收物：规范冻结之后照着规范写的第一个模块 | 0.5.0 |
| `CHG-0011` | 落地 **RQ-T-10/20** 的对外接口（评语轨 + 出题辅助）。两处刻意的省略：评语 schema **无 `score`/`evidence` 字段**，出题辅助**无 apply 参数** | 0.6.0 |
| `CHG-0012` | 逐用例证据补全：`resource`（cgroup 实测）/ `artifacts`（文件摘要与变更清单）/ `explanation`（规则式解释）。**实测：在 `case_evidence` 里插入模型输出字段会被 schema 拒绝** —— "模型输出进不了报告"是结构性的 | 0.7.0 |

### 10.2 连"唯一被允许写锁的工具"都漏了 B5

`relock_contracts.py` 的 `MODULES = ("B1","B2","B3","B4")` 是字面量。后果具体：**B5 对唯一能写锁的
工具不可见**，只能带一份手写摘要 —— 而那正是 ADR-0013 与"摘要必须由工具计算"要防的事。
已改为调用门禁同一个 `discovered_modules()`，两者从此不可能不一致。

### 10.3 文档合规：`docs/api/*` 曾与七份契约变更完全脱节

MAIN 自己的同步规则要求契约变更同步 `docs/api/*`，而实测该目录对 `securitySchemes`、新端点、
新模块**零提及**。已补：

- `governance.md` §2.4.1 记录**鉴权声明的约定**（默认受保护、例外只允许三类、服务间用
  `serviceToken` 且值是完整 header —— 含那条**实际造成过事故**的说明），并明确**权威在契约**、本节不复制
  scheme 定义；
- §3 补 B5 的端点所有权、路由双向对账的语义（契约写**完整路径**、`module.yaml` 写**前缀**）、
  以及"模块数量开放"的含义；
- §4 的内部端点清单补上 fixture 上传与 **存证端点**，并写明存证为什么是内部的（执行者与记录者分离）。

### 10.4 A6 证据台账：让文档里的测试数字可核验

文档引用的测试总数（"1145 passed"、"23 文件 / 133 用例"）此前是**手抄散文**。没人能发现真正会发生的
情况：契约变了、套件没重跑、而数字依然描述一个已经不存在的版本 —— 它看起来仍然合理，这比明显错误更糟。

`scripts/test_evidence.py` 把每次运行记录成机器可读产物，**绑定当时**的契约版本与摘要；
`--check` 在证据缺失或过期时失败。两条刻意的设计：

1. **没有手工写入的路径**：唯一的写入者解析真实运行输出。可手写的证据文件等于第二个可以陈述主张的地方。
2. **它不并入 `checks.py`**：否则每次契约变更都要重跑五个套件（还需要服务），会把日常门禁变成不可用。
   它是**交付期**门禁。实测已记录 B1：`133 passed @ 0.7.0 / 4ad48fbbd710…`。

### 10.5 本轮实测读数

```text
contracts, manifests and requirements: 依赖 check_module_installations —— 代理写入期间会红
references / change records / route declarations / requirement baseline: 全部 ok（0.7.0）
test_evidence --check: B1 当前；B2/B3/B4/B5 未记录
```

### 10.6 未完成

- **B5 一致性套件**、**E1 容器路径**、**B3 创新簇**、**B4 的 domain-event stream 与 `student_id`**、
  **B2 wave-2（评语轨 + 逐用例证据 + 沙箱自证）**：代理进行中。
- `CHG-0011` / `CHG-0012` 的**实现侧验证全部未完成**（白名单结构性证据、删评语后重放分数不变、
  默认关闭无出网调用、建议不改题、真实 cgroup 峰值落库）。
- 五个套件的证据只有 B1 已记录。
- **运行中的 host 与 worker 仍是旧代码**：host 待全部代理结束后重启；**worker 必须等 domain-event
  stream 建好后再启**（每跑一遍 `attempts += 1`，五遍会把积压死信）。

---

## 11. 契约 0.8.0 → 0.9.0、"写进去读不出来"这一类缺陷（第八至十一轮）

### 11.1 三份 CHG 与一条反复出现的模式

| CHG | 内容 | 版本 |
|---|---|---|
| `CHG-0013` | **B3 质量能力的出口**：诊断端点、`revision_note` 读写、`tags` 检索参数 | 0.8.0 |
| `CHG-0014` | **隔离区复核台与证据包**：`/api/v1/intake/quarantines` 读+归属、`appeal-pack` | 0.9.0 |

这两份与 `CHG-0007`（作业/名单无 schema）、`CHG-0008`（`tags` 只能写不能查）是同**一类**缺陷：
**能力实现了，出口不存在**。功能在库里、在代码里，但对使用者不可见 —— 交付上等同于没做。
三次都是由模块侧按规程停下上报，而不是自行加未声明的字段或参数（那会重演审计的 V14 缺陷）。

`CHG-0014` 的两处尤其典型：
- `b2.intake_quarantines` **自邮件接收路径建成起就在写**，却**没有任何读取路径** —— 审计的措辞
  很准确：从教师角度看，未匹配的邮件**无声无息地不见了**。学生说"我交了"、教师查不到、
  系统里也没地方查得到。这不是少一个页面，是**交付链断了一环**。
- 成绩争议的证据**不可移动**：复核者没登录本部署就无法判断判分是否成立。

### 11.2 发布门禁带来的一个真实后果：验证是工具侧动作，不是 UI 按钮

`CHG-0008` 让发布必须有参考解答验证存证，而这条门禁是**真的**。一个必须讲清的后果：
**B1 的出题界面无法自行完成发布**，因为存证的整个设计前提是**执行者与记录者分离** ——
B3 禁止执行任何东西，跑参考答案的是 MAIN 的题库工具；若让客户端声明"我验证过了"，
存证就退化成自我声明。

已把完整路径写进 `seed/question-bank/README.md`（草稿 → 工具验证 → 记录存证 → 发布，导入器合成后三步），
并明确告知 B1：**不要渲染一个总是失败的发布按钮**，把草稿显示为"待验证后才能发布"并说明原因。
若日后要教师自助发布，正确做法是**增加平台侧的验证编排**（能执行代码的组件跑、B3 记录），
而不是让前端自己声明。

### 11.3 门禁又一次当场抓到我在同一处犯的错

新增 `/api/v1/intake` 端点时，`check_route_declarations()` 立刻报出
"contract path is under no module route prefix"。已在 `B2/module.yaml` **与** B2 descriptor
**两侧**补入（B4 注册表逐项比对，两边都必须有）。

同一轮还撞上第 5 轮发现的那个陷阱：改完 `B2/src/.../plugin.py` 后 import 到的仍是**已安装副本**，
于是 descriptor 看起来没变 —— `check_module_installations()` 的存在意义正在于此。

### 11.4 A1 的原始交付物已关闭：即插即用验证规程不再是"全部未执行"

`runbooks/integration.md` §6 的八行此前**全部**是"未执行"。现在八行都有可复现证据
（B5 的 28 passed 一致性套件 + MAIN 门禁），并**逐条写明三项未执行**：真实端口 e2e
（套件走 `TestClient`）、跨机部署、以及 **HTTP 方法级比对** —— B5 比对的是**声明**
（契约前缀 vs 描述符前缀），不是对运行中的服务逐个探测方法，所以"契约声明 GET、实现只支持 POST"
这一类差异**不会被发现**。这个限制由 B5 自己指出。

### 11.5 A6 证据台账补齐交付扫描

`scripts/test_evidence.py --all`：按序跑五个套件、各自记录，**不因第一个失败而停下**。
配套地，这一轮我自己在 `edit` 上又犯了同一类失误两次（把两行 YAML 粘成一行），两次都由
**严格 YAML 门禁**在下一步拦下（`expected <block end>, but found '<scalar>'`）。

### 11.6 未完成（截至本轮）

- **进行中**：B2 wave-2（评语轨、逐用例 `resource`/`artifacts`/`explanation`、沙箱自证）、
  B4（domain-event stream、`student_id`）、E1 容器路径、B3 的 CHG-0013 接线、B1 wave-3
  （出题界面 D6 + 证据可视化 C6）。
- **未开始**：质量热力图（簇 B 最后一项）。
- **`CHG-0011`…`0014` 的实现侧验证全部未完成**，不得表述为已通过。
- **安装新鲜度门禁在代理写入期间必然是红的**（四个模块的已安装副本落后于工作树）；
  这是该门禁的职责，不是回归。它在代理结束后会自然转绿，**故意不在中途重装**，
  否则会把代理正在写的半成品装进 venv。
- host 重启与 worker 启动仍待全部代理结束；worker 必须等 domain-event stream 建好。

---

## 12. 重启就绪探测与一次"标准本身错了"的裁决（第十二至十三轮）

### 12.1 组装能力此前只在重启那一刻才被检验

host 一旦启动就长期驻留，于是"这套代码现在还能不能组装起来"平时没有答案，**代价在重启时一次性支付**：重启失败等于把在线的服务停掉。`create_app()` 会完整走一遍装载（模块发现与按能力定序、各模块 settings view、路由注册、静态挂载）但**不绑定端口**，因此这是一个可以在服务继续对外时安全运行的检查。

新增 `MAIN/scripts/probe_host_assembly.py`（§3.6）。它在代理写入期间给出的读数是：

```text
host assembly FAILED: RegistryError: B2: module.yaml and the runtime descriptor disagree --
descriptor.contract_version='0.7.0' but module.yaml declares '0.10.0';
routes: descriptor=[...3 条] module.yaml=[...4 条, '/api/v1/intake']
```

**这是正确失败**，不是回归：它说明当时**重启会失败**，而修复动作是重装、不是改声明。它同时回答了另一个问题——门禁红的时候，究竟是"代理还没写完"还是"写了但没装"，这两种情况的处置完全不同。

同一次检查还顺带暴露了一个只有 MAIN 能发现的问题：文档门禁报出

```text
B3/docs/phases/active/phase-04-quality-surfaces.md: dead link -> ../api/contract.md
```

`B3/docs/api/contract.md` 确实存在，所以这是相对深度差一层（应为 `../../api/contract.md`）。该文件属正在运行的 B3 代理，已把复现命令与该门禁的通过判据一并交给它修，而不是与它的写入竞争。

### 12.2 `E16`：69 条迁移 DDL 违规里，有一部分是"规则错了"

给这一项定量后（`uv run --directory B4 --no-sync python -m autograder_b4.ops.table_standards --scope migrations`）：

```text
migrations: 137 DDL statements in 16 files, 69 violation(s)
```

按类型分组后，结论与"69 条待修"完全不同：

| 类型 | 数量 | 裁决 |
|---|---|---|
| 主键约束未命名（要求 `pk_<table>`） | 35 | **真违规**，应修 |
| `timestamptz` 未声明毫秒精度（要求 `(3)`） | 22 | **真违规**，应修 |
| 复合主键被判违规 | 5 | **规则错误** |
| 表名非复数（`event_inbox`、`event_outbox` 等 3 处） | 3 | **规则错误** |
| 主键列非 uuid（`blobs.content_sha256` 是 `varchar(64)`、`consumption_counters.outcome` 是 `varchar(32)`） | 2 | **规则错误** |
| 主键列名不以 `_id` 结尾（同上两列） | 2 | **规则错误** |
| **合计** | **69** | 真违规 57 / 规则错误 12 |

B4 的 `docs/database/table-standards.md` §2/§3 要求**每一张表**都有单列 `<entity>_id UUID` 主键，且明文"推断被禁止：未声明的表直接判违规"——**没有任何豁免**。于是它对下列三类合法设计给出了错误判决：

- **内容寻址存储**：`blobs` 的主键就是 `content_sha256`。改成 uuid 代理键会让"按内容去重"这一存在理由消失，是**功能倒退**。
- **复合自然键**：`question_version_score_buckets(question_version_id, bucket_label)`、`reference_attestations(question_version_id, content_digest)`、`question_version_failure_categories(question_version_id, failed_category)` 的自然唯一性**就是**这两列的组合；加代理键只会引入一个没有语义的列和一个多余的唯一约束。
- **outbox / inbox 表**：`event_outbox` / `event_inbox` 是这一模式的通用命名（且并非可数名词），复数化没有意义。

值得注意的是该标准自己在 §3 承认复合**唯一**约束（`sections(course_id, section_code, term)`），却把复合**主键**一律判违规——同一个建模手段，一处允许、一处禁止。

**处置**：69 条中 **12 条属"规则过泛"，不修 schema**；**57 条是真违规**。把 69 条一律"修绿"会把内容寻址表和自然键表改坏，因此这一项**不以违规数归零为完成条件**。正确的修法是给标准补上这三类原型并让检查器识别它们，同时不削弱其余规则（另见 §7 遗留项）。这是跨模块标准，按 `AGENTS.md` 的裁决职责由 MAIN 判定，不由 B2/B3 各自绕开。

### 12.3 域事件流建成后的独立核对：B2 通了，B3 的发布端从未运行过

B4 建好 `DOMAIN_EVENTS` 并投递 B2 的 15 条积压后，我没有只采信报告，而是直接查集成库（`main-postgres-1:55432`，注意**不是**各模块的测试实例 `55433/55434/55435`——那里没有这些表）：

```sql
select 'b2.outbox_events', count(*) filter (where published_at is null), count(*) filter (where published_at is not null), coalesce(max(attempts),0), count(*) filter (where dead_lettered_at is not null) from b2.outbox_events
union all select 'b3.event_outbox', ... from b3.event_outbox
union all select 'b3.event_inbox', ... from b3.event_inbox;
```

```text
            tbl            | unpublished | published | max_attempts | dead_lettered
---------------------------+-------------+-----------+--------------+---------------
 b2.outbox_events          |           0 |        15 |            1 |             0
 b3.event_outbox           |         103 |         0 |            0 |
 b3.event_inbox (received) |             |         0 |              |
```

**B2 侧完全证实**：待发行 0、已发行 15、`max_attempts` 仍为 1（没有重试膨胀）、死信 0。

**同时查出一个报告里没有的事实**：`b3.event_outbox` 有 **103 条待发行、已发行 0、`attempts` 为 0**。`attempts` 为 0 而不是某个正整数，说明**从未有任何进程尝试过投递**——不是投递失败，是从未运行。103 这个数字与题库已发布版本数吻合，即题库导入时写的发件箱事件一直躺着。

定位到原因（这是运维缺口，不是代码缺陷）：`drain_outbox` 全仓只有一个调用点，在 `B3/src/autograder_b3/consumer_cli.py:58`，也就是 **B3 的中继与消费者同进程**。该进程从未启动，因此 B3 的发布端与消费端都没动过。B3 自己的 `docs/api/events.md` 已如实写明投递由 `drain_outbox` 负责，所以这不是"文档说做了但没做"。

**处置**：列入最终集成序列——重装 B3 之后启动一次 `python -m autograder_b3.consumer_cli`，它会在同一次运行里完成两件事：把 103 条积压投递到 `DOMAIN_EVENTS`，并开始消费 `evaluation.completed`。这同时也是 B3 发件箱的**首次真实验证**（此前只有测试里的验证）。注意这**不会**让 `b3.event_inbox` 动起来：那需要 B2 真正完成一次评测，而 B2 的评测需要 Linux nsjail——本机只能返回 `infrastructure_error`。两件事必须分开陈述，不得因为 103 条投递成功就暗示消费链路也验证了。

### 12.3.1 把上面这条查询固化成工具，随即查出第三个模块也一样

上面那段 SQL 是一次性的，于是写成 `MAIN/scripts/event_chain_status.py`：按 `information_schema` **发现**各模块的 outbox/inbox 表及其列（MAIN 不拥有模块物理表，因此既不硬编码表名列名，也不复制 DDL；缺列就报 `n/a` 而不猜），再读 JetStream 各 stream 的 `messages`/`bytes`。一条命令给出整条链路的读数：

```text
table                         unpubl    publ  attempts   dead    recv    proc
b2.inbox_events                  n/a     n/a       n/a    n/a       0     n/a
b2.outbox_events                   0      15         1      0      15     n/a
b3.event_inbox                   n/a     n/a       n/a    n/a       0       0
b3.event_outbox                  103       0         0    n/a     103     n/a
b4.inbox_events                  n/a     n/a       n/a    n/a       0     n/a
b4.outbox_events                   2       0         0    n/a       2     n/a

JetStream streams:
  B4_DLQ:        messages=0  bytes=0
  DOMAIN_EVENTS: messages=15 bytes=9685  subjects=[submission.accepted, evaluation.started,
                 evaluation.progress, evaluation.completed, evaluation.failed,
                 question.version.published]
  ROSTER_IMPORT: messages=0  bytes=0
```

`DOMAIN_EVENTS` 的 15 条 / 9685 字节与 B4 的报告逐字吻合。**但同时查出 B4 自己的发件箱也有 2 条从未投递**，这是 B4 报告里没有的事实：

```text
              event_type               | attempts | published_at | last_error |      occurred_at
 autograder.v1.roster.import.completed |        0 |              | (none)     | 2026-09-18 11:56:31+00
 autograder.v1.roster.import.completed |        0 |              | (none)     | 2026-09-18 11:58:16+00
```

B3 的 103 条同性质，全部是 `autograder.v1.question.version.published`，全部发生在题库导入当天。

**核对后判定：两者都是运维缺口，不是代码缺陷。** B4 有中继入口 `uv run python -m autograder_b4.ops.messaging relay`（`ops/messaging.py` 顶部写明"这些进程**故意**与 composition host 分开运行"），只是从未启动；三条 stream 里 `ROSTER_IMPORT` 的 `messages=0` 与此一致。关键在于 `attempts` 为 0 且 `last_error` 为空**同时**出现：投递失败会留下 `last_error` 并让 `attempts` 增长，两者都空说明中继进程一次都没跑过。这个区分很重要——"发件箱有积压"既可能是实现坏了，也可能只是没人启动中继，而只有这个组合能区分二者。

**汇总**：三个发布方中**只有 B2 的中继被真正运行过**（B4 为了核对 B2 的积压跑过一次）。B3 与 B4 的中继都需要在最终集成里启动，而启动它们本身就能完成一次**不依赖 Linux** 的真实端到端验证：B3 的 103 条 + B4 的 2 条应全部转为已发行、`attempts` 为 1、死信 0。

### 12.4 最终集成序列（顺序不可交换，理由随步骤）

代理写入期间 `checks.py` 的安装新鲜度门禁必然是红的（已安装副本落后于工作树），**故意不中途重装**：那会把代理正在写的半成品装进 venv，并让后续所有导入型门禁去验证一个中间的 revision。顺序如下：

1. **等全部代理结束**（写入停止）。
2. **重装四个模块**：`cd MAIN; uv sync --all-groups --extra modules --inexact --reinstall-package autograder-b2 --reinstall-package autograder-b3 --reinstall-package autograder-b4 --reinstall-package autograder-b5`。
3. **`probe_host_assembly.py`**：必须输出 `host assembled`，否则**先修再继续**——此时重启会失败。
4. **`checks.py`**：三条通过判据全绿（含安装新鲜度、契约摘要、需求基线）。
5. **各模块测试套件**：`scripts/test_evidence.py --all`，逐模块记录。
6. **重启 host**（当前 `pwsh-10` 跑的是旧代码）：先停、再起、再核对 `/health/ready` 的模块与 `CHG-0011..0015` 的路由确实在线。
7. **启动三个中继与 worker**（三个发布方中此前只有 B2 的中继被运行过，见 §12.3.1）：
   - B2 worker：必须用修好的代码（旧进程已终止，它跑的是 `dispatch` 未被 await 的版本，且把真实提交判为 `failed`）。
   - B3：`python -m autograder_b3.consumer_cli`（中继与消费者同进程）→ 103 条 `question.version.published` 应转为已发行。
   - B4：`uv run python -m autograder_b4.ops.messaging relay` → 2 条 `roster.import.completed` 应转为已发行。
   - 三者完成后用 `python scripts/event_chain_status.py` 一次性核对：所有 outbox 的 `unpubl` 应为 0、`attempts` 为 1、`dead` 为 0。
   - **仍不会变的是 `b3.event_inbox`**：它需要一次真实完成的评测，而评测需要 Linux nsjail。不得因为发布端全绿就暗示消费端已验证。
8. **Playwright 端到端**：走真实浏览器。
9. **实现侧验证 `CHG-0011`…`0015` 的约 20 条断言**——这是唯一一项至今**完全未执行**的验证，不得跳过。

### 12.5 簇 E「分页静默丢数据」：修了一半，另一半是 UI 不诚实（MAIN 复核发现）

复核 B1 的实现后确认：`src/api/pagination.ts` 的 `useLoadAllPages` **正确**同时返回 `{ complete, capped }`，三个 hook 也都**拒绝**用不完整数据下结论（`incomplete` / `complete && !capped`）。这一半是对的。

但三个 hook 都把 `capped` **并入了"仍在加载"**，于是调用方无法区分二者：

| 位置 | 合并写法 |
|---|---|
| `src/features/grades/useGrades.ts:89` | `incomplete: !complete \|\| capped` |
| `src/features/grades/useCohort.ts:85-86` | `!gradesLoad.complete \|\| gradesLoad.capped \|\| !rosterLoad.complete \|\| rosterLoad.capped` |
| `src/features/assignments/useAssignments.ts:84` | `complete: complete && !capped` |

消费端（`SubmissionMatrix.tsx:81-87`、`AssignmentsPage.tsx:301-304` 与 `:467-469`）对这个合并态一律渲染 `<Spinner/>` + `loadingAll`。而 `capped` 是**永久**的（`MAX_AUTO_PAGES = 40` 重试不会变大），所以一旦真的触顶，用户看到的是**一个永远不会结束的加载动画**，标签还写着"正在加载全部"。

**判定**：这不是"已修"，而是把同一个静默丢数据的缺陷从"给出错误结论"搬到了"永远声称还在加载"。演示数据到不了 40 页，因此该分支现在**没有任何测试覆盖**，也从未被真实触发。

**处置**：已把精确的文件与行号、以及"触顶时必须渲染明确的截断提示且**不得**出现 Spinner、并且仍不得渲染任何未提交判定"的要求交给正在运行的 B1 代理，并要求补一个把 `pageCount` 推到上限的回归测试（`pnpm verify` 必须干净）。此处记录以免该发现随代理结束而丢失。

### 12.6 本轮 MAIN 门禁快照（10/13 绿，三个红全部可归因）

`python scripts/gate_status.py --quiet`：

```text
PASS  check_contract_examples
PASS  check_contract_parses_strictly
PASS  check_manifests
FAIL  check_module_installations
FAIL  check_module_descriptors
PASS  check_contract_digest_implementations
PASS  check_requirement_baseline
PASS  check_decision_references
PASS  check_change_records
PASS  check_route_declarations
PASS  check_module_governance
FAIL  check_document_encoding_and_links
PASS  check_local_configuration_consistency

10/13 gates pass
```

三个红门禁的读数与归因（**没有一个是需要修的真实缺陷**）：

| 红门禁 | 读数 | 归因 |
|---|---|---|
| `check_module_installations` | B2/B3/B4/B5 已安装副本落后于工作树 | 四个代理正在写；重装是最终集成第 2 步 |
| `check_module_descriptors` | 四个模块一律 `descriptor.contract_version='0.7.0'` 而 `module.yaml` 为 `0.10.0` | **同一个根因**：描述符来自已安装的旧代码。它与上一条不是两个问题，重装后一起转绿 |
| `check_document_encoding_and_links` | 5 处指向 B2 `phase-03-commentary-and-per-case-evidence.md` 的死链 | B2 正在写该文件（引用数在本轮内从 2 增到 5，说明是在写而不是写漏） |

**契约与需求侧全绿**：摘要实现、需求基线（31 条）、决策引用、变更记录、路由声明、模块治理、本地配置一致性——这些都不依赖已安装代码，因此在代理写入期间仍然是可信的。

同时确认了两项**已经在位**的簇 C/E 交付（复核而非采信）：草稿身份隔离是真的（`submission/draft.ts` 的 `parseDraft` 在读出后**重新校验** `ownerId` 与 `questionVersionId` 并逐字段类型检查，不只靠 key 隔离，且有跨学生与篡改载荷的测试）；而分页那一项见 §12.5，只修了一半。

### 12.7 E2 只修了一半：门禁读一份文档，另一份就漂移

`E2` 原本记的是"MAIN 全局追踪矩阵状态列 32/32 全错"。早先的处置是让 `traceability.md` 的状态列**由 `scripts/sync_requirement_status.py` 生成**、并由 `requirement_status_drift()` 强制。复核时发现 MAIN 有**两份**文档陈述需求状态，而门禁只覆盖了其中一份：

```text
rq-catalog.md: RQ-B-0-10: no legal status word (one of 骨架/待实现/已完成/阻塞) in '| `RQ-B-0-10` | 全体 | ... | — | 契约已锁定 |'
rq-catalog.md: RQ-B-1-10: states 待实现 but the owner declares 已完成
...（32 行全部漂移，traceability.md 一行都不漂移）
```

`rq-catalog.md` §0 是**最该准的那一份**——它开头自称"本文件是全部 32 条课程需求的**权威目录**"，因此是评审最先打开的文件——却整份停留在 Phase 00 的快照上：32 行全写 `待实现`／`骨架`。更糟的是 `RQ-B-0-*` 三行用了 `契约已锁定`，**它不是四个合法状态词之一**，因而在解析上等同于"没有状态"，连"写错了"这件事本身都是隐形的。

根因不是疏忽，是**覆盖缺口**：门禁查 A 文件，B 文件就自由漂移。这与 §12.2（标准过泛）、§12.3（中继从未运行）是同一类问题——**"有机制"和"机制覆盖到了"是两件事**。

**修法**（两根一起修，否则会再次漂移）：

1. `checks.main_matrices()` 显式列出全部 MAIN 矩阵，`requirement_status_drift()` 改为逐个扫描。每行必须**恰好**含一个合法状态词——于是自造词是失败而不是静默通过——且必须等于拥有者的声明（`RQ-B-0-*` 无模块拥有者，取 MAIN 自己的声明；对 `traceability.md` 这是同义反复，对 `rq-catalog.md` 才是真正的比较）。
2. `sync_requirement_status.py` 同时重写两份文档。状态列在每份文档中的**位置是显式声明的**（矩阵按"哪个单元格是状态词"，目录按"最后一列"），不靠猜；位置对不上是报错而不是跳过。

**变异证明**（三种失败模式，文件在 `finally` 中恢复；脚本用完即删）：

```text
baseline drift entries = 0
CAUGHT  catalogue: legal word, wrong value: drift entries = 1
CAUGHT  catalogue: invented vocabulary: drift entries = 1
CAUGHT  matrix: legal word, wrong value: drift entries = 1
restored clean: drift entries = 0
```

第三项是刻意的：扩展不能把门禁**原有**的覆盖弄丢。修完 32 个单元格后重跑生成器输出"already match"（幂等），`check_requirement_baseline` 通过，31 条需求基线未变，`契约已锁定` 出现次数归零，中文编码无损坏。`RQ-B-0-20` 如实保留为 `骨架`（冻结表已成文，但"符合课程文档"需课程方确认）。

顺带在 §0E 其余条目上做了能当场核实的复核并就地标注：`E3`（四份契约的文档级 `security:` + 三种覆盖各有归属，`serviceToken` 无未引用声明）、`E4`（摘要实现收敛到 SDK 且门禁为绿）、`E5`（`ADR-0008` 存在且引用门禁为绿）、`E14`（`as_new_version` 已声明）。`E10` 复核发现保留策略**已经实现**（`B2/src/autograder_b2/storage/gc.py`：可配置窗口、不删仍被引用的内容），但验收 5 还要求"清理操作有审计记录"，这一条**我没有验证**，因此不标注为已修。

### 12.8 簇 E 收口：16 条中 14 条已关闭，每条都在代码里复核过

上一轮我只标注了自己当场核实过的 5 条。本轮把 §0E **全部 16 条**逐条在**代码/契约/门禁**层面复核了一遍（不采信代理报告的结论，只采信能自己读到的证据），结果 14 条关闭、2 条仍开：

| 条目 | 复核方式与关键证据 |
|---|---|
| E1 | 已关闭（早先）。容器路径已是文档化交付路径 |
| E2 | 已关闭。见 §12.7——本轮补完，含变异证明 |
| E3 | 已关闭。四份契约**文档级** `security:`；B4 三个公开操作 `security: []`；B3 三个内部操作 `serviceToken`，与全仓恰 3 处引用吻合，无未引用声明 |
| E4 / E5 | 已关闭。实现收敛到 SDK；`ADR-0008` 存在；两条对应门禁均为绿 |
| E6 | 已关闭。`seen_declarations` 让重复声明名**响亮失败**（ADR-0013 决定 5），文档写明"同一输入两个相反答案"的历史成因 |
| E7 | 已关闭。`MAIN_ROOT = Path(__file__).resolve().parents[3]`，两个分支都相对它解析 |
| E8 | 已关闭。三个配置名在 `B4/src` 的读写点均为 **0** |
| E9 | 已关闭，且是**结构性护栏**：`scoring.py:122` 对安全阻断的运行抛 `ScoringPreconditionError`，**给分在代码上不可能发生**；`security_check` 三态，"未检查 ⇒ `failed`"（fail-closed） |
| E10 | 已关闭。见上；本轮补验了"审计记录"一条：`artifact_gc_audits` 按 `gc_run_id` 落行，`test_storage_gc.py` 真实断言，`test_migrations.py` 钉住 CHECK 与外键。**回收的原子性值得单独指出**：年龄条件与引用条件写在**同一条 `DELETE`** 里，判定与删除之间没有时间窗 |
| E12 | 已关闭。账本行 268435456 约束三条流**之和**，`ROSTER_IMPORT_MAX_BYTES` 由**减法推导**（两个手写字面量正是原来超账本的原因） |
| E13 | 已关闭。三类发射点齐备（migration/backup/health），且健康事件只在状态变化时发射 |
| E14 / E15 | 已关闭。见 §12.7 与上表；E15 的 B1 文档已自我改正并给出状态分布 |
| **E11** | **仍开**（部分修复）。三条缺陷描述已各自变化：已有实测（0.044 s / 23.8 s / 8.327 s 三条基线）、窗口路径已分页、`list()` 已加限；但**窗口路径 23.8 s 仍不达标**，B3 记为 `已完成（验收 5 未达标）`——这个口径正确，不得抹平 |
| **E16** | **仍开**。标准过泛部分已裁决并交回 B4 修（§12.2）；57 条真违规属 B2/B3，尚未派发 |

**复核中发现的新缺陷（已派发）**：`B3/docs/definition-of-done.md:65` 仍写"未满足（**无基准**）"，而同模块的 `README.md`／`overview.md`／`api/events.md` 都已列出基准——**同一模块内自相矛盾**，且这是"判定写于证据出现之前"这一类。已要求 B3 修正，并明确两条约束：**不得因为补上基准就升级结论**（验收 5 仍未达标），且**不得再复制一份数字**（改指向 `phase-03` 的权威位置），否则同一个数字又会有两处可漂移。

**E16 的派发时机是刻意的**：B4 正在把三类合法原型写进标准与检查器，真实违规数会随之变化；此刻按旧数字派发，两个模块会照着一个即将过期的清单干活。

### 12.9 E16 标准已修正、57 条真违规已按模块派发

B4 完成标准修正后的实测（同一命令，B4 自报并经 MAIN 核对分组口径一致）：

```text
补全前: migrations: 137 DDL statements in 16 files, 69 violation(s)
        35 未命名主键 / 22 精度 / 12 误判（5 复合键 + 3 非复数 + 2 非 uuid + 2 非 _id）
补全后: migrations: 137 DDL statements in 16 files, 57 violation(s)
        35 primary key is unnamed; the standard requires 'pk_<table>'
        22 declares timestamptz without millisecond precision; the standard requires timestamptz(3)
原型相关残留: 0 条
--scope catalog: b4 schema conforms  (0 违规)
```

即 **12 条误判全部消失、57 条真违规一条未减**——这正是我要求的方向，而不是"把数字改小"。

**B4 的修法里有一处值得记下的设计**：原型声明按 **(模块, 表名)** 键控，而不是按表名。这不是风格选择，是被实测逼出来的——`inbox_events` 在 **B2（复合键 `(consumer, event_id)`）与 B4（代理键 `inbox_id`）同时存在**，按表名键控会**同时**误判 B4 自己的表并错误豁免另一张。B4 自己先写错、又被自己模块的迁移当场报出来，随后改成模块键控。每条原型都是**声明 + 结构前置条件**双查，因此"声明"不能给一张表洗白：`payloads(digest varchar(64) PRIMARY KEY)` 未声明仍被报；`event_inbox_backup`（名字相近）仍被报非复数；声明了 `blobs` 但摘要列只有 `varchar(30)` 且无 64 位检查则报前置条件。**三方向测试**（正确写法不报 + 相似写法仍报 + 声明不能洗白）都在 `tests/test_schema_standard.py`；B4 套件 227 passed、`ruff check .` 干净。

**57 条按模块派发**（B2 25 = 18 未命名主键 + 7 精度；B3 32 = 17 + 15），各自新增**一条纠正性 revision**，明确要求：

- **不改已应用的 revision**——集成库 `55432` 已有播种数据（103 个已发布版本、106 道题、377 个测试用例），改写已应用的 revision 会让 `alembic_version` 与实际 schema 脱节；
- `downgrade()` 必须**真正可逆**（要求实测 `upgrade → downgrade -1 → upgrade` 三步，而不是"有个 downgrade 函数"就算）；
- 约束重命名**不得假定隐式名 `<table>_pkey`**，要查 `pg_constraint`，否则在全新库上会失败；
- `timestamptz(3)` 会**丢弃已有行的亚毫秒精度**，必须在迁移 docstring 里如实写明，不得描述成 no-op；
- **只在各自测试实例上跑迁移**（B2 55433 / B3 55434），集成库由 MAIN 统一施加——共享的播种环境不由模块擅自改动。

同时明确告知 B3：`blobs`（内容寻址）、`consumption_counters`（枚举自然键）、三张复合自然键表、`event_inbox`/`event_outbox`（非复数）**已按声明豁免**，不属于它的 32 条，**不得"顺手修"**。这条例外清单必须说清楚，否则一个只看到"违规数"的模块会把正确设计改坏——那正是我在 §12.2 裁决时担心的事。

### 12.10 CHG-0016：一次"同一个 URL 两种实体形状"的裁决，以及一次**被驳回**的变更请求

B3 在 CHG-0013 落地过程中上报了两项契约问题。两项都回到 MAIN，裁决结果相反。

**驳回的一项**：B3 称"契约的 `ComparisonDiagnostics` 描述把 `severity` 第三值写成 `note`，而枚举是 `info`，属契约笔误"。全量检索 `MAIN/contracts/**`（含 `schemas/*.json`）后**该笔误不存在**——三处 severity 枚举全部是 `[error, warning, info]`，`note` 只出现在三处无关散文里。B3 据此把实现里的 `Severity.NOTE` 改成 `INFO` **是对的**（`note` 确实无法序列化），但正确描述是**"实现偏离契约"**，不是"契约笔误"——两者是不同的发现、不同的责任方。已要求 B3 更正其文档措辞，并把这次驳回写进 CHG-0016 §5 与 `contracts/CHANGELOG.md`，**专门防止后人照着"存在一处 note/info 不一致"去改动一个本来就正确的声明**。记录一次"不做某事"与记录一次改动同样是交付内容。

**接受的一项**：`PATCH /api/v1/questions/{question_id}` 的 200 声明为 `QuestionVersionPublic`，而同路径 `GET` 声明为 `QuestionPublic`，实现自 Phase 01 起两条都返回后者。

判据不是"谁改起来省事"，而是**声明是否可表达**：`QuestionDraftInput` 可写 `title`（必填）与 `tags`，而 `QuestionVersionPublic`（`additionalProperties: false`）**两者都没有**。**一个无法表达"请求刚刚改动的字段"的响应 schema 就是错的。** 由此判定契约是错的一边、实现是权威的一边。

**这个错误不是潜在风险，而是已经发生的缺陷**：B1 的出题写路径（`useAuthoring.ts:67-84`）按生成的类型读取 `version.question_version_id` 与 `version.version`，二者在 `QuestionPublic` 上都不存在，因此**运行期恒为 `undefined` 并被写进审计日志**；而 TypeScript 因契约声明错误而**无法发现**。一个错误的契约声明把编译期本可捕获的错误变成了运行期的静默错误——这正是"契约是唯一权威"要防的事，也说明该缺陷的代价不是文档不整齐。

**处置**：CHG-0016，**P2 语义 → 0.11.0**（照 CHG-0009 的先例，同为"改正错误声明"），只改那一个 `$ref`；`QuestionVersionPublic` 不动（它对 `/question-versions/{id}` 仍然正确）。**不给响应加形状开关或 `oneOf`**——两种形状并列会强迫每个消费者处理一个永不出现的分支，那是把契约错误转嫁给所有客户端。B3 无需改动；B1 改为消费 `QuestionPublic` 并从 `versions[0]` 取版本信息，且必须**断言审计字段是真实值而非 `undefined`**（只断言"调用成功"的测试在修好之前也会通过，证明不了任何事）。

**过程中我自己犯了一个错，值得记下**：升版本号的脚本里，正则匹配了 `^  version: 0.10.0$`（含两个前导空格）而替换串**没有写回那两个空格**，于是六个契约文件的 `info.version` 落到了第 0 列，YAML 结构被破坏（`mapping values are not allowed here`）。更值得记的是**我的自检没能抓到它**：脚本断言"六个文件声明的值一致"，而那只验证了**值**、没验证**结构**。抓到它的是 `relock_contracts.py` 里的严格 YAML 解析。修复脚本补上了结构断言（真正 `yaml.safe_load` 并读回 `info.version`）。这与 E2 的教训同型：**断言写得太弱，等于没有断言。**

**验证**：五份锁与五份 `module.yaml` 同步 0.11.0（新摘要 `bf2e4af4bc44…`），`check_manifests` 由红转绿；门禁回到 11/13（仅剩两条安装陈旧）。**两条实现侧验证仍未完成**——B3 断言端点真实返回键集合等于 `QuestionPublic`、B1 断言日志字段为真实值——已分别派发并在 CHANGELOG 与 CHG-0016 §6 里写明"未完成"。

### 12.11 同一个值有 14 份拷贝，而我漏掉了其中 4 份——补上门禁而不是补上记忆

契约版本这一个值，在本仓库里有 **14 处**拷贝：5 份 `contracts.lock`、5 份 `module.yaml`、4 个 `ModuleDescriptor`（`B2`–`B5` 的 `src/autograder_bN/plugin.py`）。CHG-0016 我更新了前两类，**漏掉了第三类**。

发现它的过程本身就说明问题：B5 的一致性套件在采集阶段就报

```text
autograder_b4.registry.RegistryError: B2: module.yaml and the runtime descriptor disagree --
descriptor.contract_version='0.10.0' but module.yaml declares '0.11.0'
```

即"描述符与清单不一致"。当时我把它归因于"安装陈旧"，但**重装并不能修好它**——描述符里的版本是源码里的字面量，重装只会把同样过时的字面量重新装一遍。**我最初的归因是错的**，而错误归因会让人去做一件无效的事（重装），然后在重装之后再次撞上同一个错误。

**修法是补门禁，不是记住这件事**：`check_manifests()` 现在用 `ast` **静态**读每个模块的 `ModuleDescriptor(...)` 调用并取出 `contract_version` 字面量，与 `info.version` 比对（B1 没有描述符，返回 `None` 并被明确跳过）。**静态**这一点是刻意的：`check_module_descriptors()` 导入的是**已安装**的包，那在"组装时"是对的问题，但在**开发期间是盲的**——源码与清单都改了、只是没重装时，它比较的两边都来自那份陈旧安装，于是"一致"。新检查读源码，因此在任何东西被安装之前就能发现漏改的一半。

**变异证明**（把 B3 的描述符改回 `0.10.0`，`finally` 中恢复）：

```text
baseline: PASS
descriptor left behind: FAIL: B3: the ModuleDescriptor in src/autograder_b3/plugin.py declares
  contract_version '0.10.0' but contracts info.version is '0.11.0' -- update the descriptor too,
  not only the lock and module.yaml. No reinstall can fix this; the running host would refuse to
  assemble once the installed copy caught up.
restored: PASS
```

报错信息里明确写出"**重装修不好这个**"，因为我在真实排查中正是被这句话缺失所误导。

**同时考虑并放弃的一项**：让 `scripts/relock_contracts.py` 顺手把 `module.yaml` 与四个描述符一起写掉，把一次契约变更变成一条命令。放弃的理由不是省事——该工具已经在写模块侧的 `contracts.lock`，先例是有的——而是它要开始**改写 Python 源码**（`plugin.py`）。当前门禁已经能在源码层面**立刻**抓到漏改并给出确切处置，把流程变成自我纠正；在此之上让工具改源码，收益有限而风险是"工具悄悄改了模块代码"。若日后要采纳，应当配一条 ADR 说明工具对模块源码的写权限边界。

**这三件事是同一类缺陷的第三次出现**：`E4` 摘要算法被复制 5 份、`E2` 状态列在两份文档里漂移、本次版本号 14 份拷贝。三次的修法完全一样——**让值只有一个来源或一道门禁，而不是依赖人记得同步**。这个模式值得在答辩时单独讲，因为它不是"写错了几个字"，而是**一个会反复复发的结构性成因**。

### 12.12 提前把关键路径上的一个未知量消掉：重装机制本身是可行的

最终集成序列（§12.4）第 2 步是重装四个模块。这一步此前**从未被执行过**，而 `AGENTS.md` 明确记着一个已知风险：`uv` 的缓存目录在工作区之外，曾出现 `os error 5`（权限被拒）。如果重装命令在这里失败，整条集成会在最后一步停住——而那时所有代理都已结束，是最不该发现它的时刻。

三个模块仍在写代码（B2/B3/B1 的最新改动都在两分钟内），因此**不能**对它们重装。但 **B4 与 B5 的代理都已结束**，对它们重装既安全，又能验证机制：

```text
$ uv sync --all-groups --extra modules --inexact --reinstall-package autograder-b4
Resolved 63 packages in 16ms
Building autograder-b4 @ file:///D:/VScodeProjects/AutoGrader/B4
Prepared 1 package in 3.18s
Uninstalled 1 package in 39ms
Installed 1 package in 31ms
 ~ autograder-b4==0.1.0 (from file:///D:/VScodeProjects/AutoGrader/B4)
```

**结论：机制可行，文档里记的缓存风险本轮未复现。** 注意 PowerShell 会因 uv 把进度写到 stderr 而报 `[exit code: 1]`——**退出码在这里不可信，必须按已安装的字节来验证**：重装后 B4 的已安装描述符读回 `contract_version: 0.11.0`，且从 `check_module_installations` 的陈旧名单中消失。

顺带把 B5 也重装，于是陈旧集合从四个模块缩小到**只剩 B2/B3**（仍在写入的那两个）。这也让 B5 的一致性套件第一次能在新契约下跑起来：

| B5 套件（排除两个需要组装 host 的文件） | 修复前 | 重装 B5 后 |
|---|---|---|
| 结果 | 1 failed, 11 passed, **5 errors** | **17 passed** |

修好的是**只依赖 B5 自身**的那部分；剩下两个文件（`test_composition_load.py`、`test_load_failures.py`）依赖组装 host，因而仍要等 B2/B3 重装——它们验证的正是"这个组合能不能装起来"，**在成员本身陈旧时跑它没有意义**。

**这一步的价值不在"少了两条红"**，而在于把最终集成里唯一一个**从未执行过、又可能直接失败**的动作提前验证了。这与 §12.1 的就绪探测是同一个思路：**把只在最后一刻才暴露的失败，提前到还有时间处理的时刻。**

### 12.13 CHG-0017、一条自相矛盾的指令，以及一个门禁永远无法判定的数

#### 12.13.1 CHG-0017：省略即保留（契约 0.12.0）

B1 实现出题写路径时按规程停下上报：`QuestionDraftInput` 的 `cases` 与 `reference_answer` 都是可选字段，但契约**从未说明省略意味着"保持"还是"清空"**，它不肯在两种相反语义之间赌。核查后发现缺口比"沉默"更严重——**契约写错了**：契约的 `required` 不含 `cases`，而实现的 `cases: tuple[...] = Field(min_length=1)` 无默认值因而**必填**。实测（直接构造模型，不经网络）：

```text
omitting `cases` -> ValidationError (the request would be 422)
   ('cases',): Field required (missing)
```

这与 CHG-0016 是同类错误的**相反方向**：那次实现比契约宽松，这次实现比契约严格。

**裁决为"省略即保留"**，并**明确放弃了更小的一行改动**（把 `cases` 标成必填）。理由不是偏好：若 `cases` 必填，则**每一次**编辑——包括只改标题——都必须把**隐藏用例与参考答案**送到客户端再送回来。本项目专门做了"执行者 ≠ 记录者"的拆分来避免这类暴露，让受保护内容为了改一个标题而出一次门，与那条设计意图直接冲突。这是**两种正确性之间的取舍**：契约与实现一致（两者都能做到）对比受保护内容的最小暴露面（只有后者能做到）。

#### 12.13.2 版本同步工具化，以及门禁当场抓到我自己的顺序错误

§12.11 记下"一个值有 14 份拷贝"后，本轮把它变成了工具：`scripts/sync_contract_version.py`（`--to` 改六份 `info.version`，随后总是同步五份 `module.yaml` 与四个描述符；`--check` 只报告）。CHG-0017 用它一条命令同步完 9 处派生声明。

随后门禁**当场抓到我自己的顺序错误**：我把 `contracts/CHANGELOG.md` 放在 relock **之后**才写，而摘要覆盖 `MAIN/contracts/**` 的**全部**文件（含 `CHANGELOG.md`），于是锁立刻过期：

```text
B1/contracts.lock: contract digest mismatch
```

正确顺序是**先把契约树（含 CHANGELOG）改完，再 relock**。这条不是我记住的，是门禁告诉我的——这正是它该有的样子。

#### 12.13.3 我给两个模块下了**自相矛盾**的指令，两个模块都拒绝照着绕过

E16 我要求 B2/B3"**新增一条纠正性 revision**，**不改**已应用的 revision"，同时把完成条件定为"**linter 报 0**"。B2 与 B3 **各自独立**发现这两条不可能同时成立，都停下上报而不是想办法糊过去：

```text
B3 的 0004：文件里 4 处 ALTER TABLE；scan_migration → 0 DDL statements，0 violations
B2 的 0012：DO $$ … $$ 块；全仓 DDL 语句数 137，改动前后**完全一致**
```

我自己复核了 B3 那条读数，确认属实。原因是结构性的：`--scope migrations` 是**逐条声明的文本扫描，没有跨 revision 状态**，不认识 `RENAME CONSTRAINT` / `ALTER COLUMN TYPE` / `DO` 块，因此**永远无法认可一个事后修复**。**这条指令的矛盾是我的错**，不是模块的。

**采纳 B2 提的更好方案**（并据此更正了已发给 B3 的指令）：**保留**纠正性 revision，同时把原始 revision 的**声明文本**改成合规。因为两个纠正器都是**幂等**的（主键名从 `pg_constraint` 现查、精度从 `information_schema` 现查，我逐个核对过），这一个改动同时覆盖两个方向：

| 数据库状态 | 结果 |
|---|---|
| 已迁移（集成库 55432，按旧文本迁移，仍是 `<table>_pkey`） | 纠正性 revision 执行**修复**，无需重建播种库 |
| 全新（按改后原始声明从头迁移） | 声明本身已合规，纠正性 revision 是**no-op** |

我原先"删掉纠正性 revision"的指令会**为了一个没有收益的目的去重建播种数据库**。已要求两个模块证明 no-op 是**真的** no-op（在全新库上先 `upgrade` 到纠正器之前、断言已经合规，再 `upgrade head` 断言没有任何变化）——**假定出来的 no-op 正是我要排除的东西**。

**这同时是一条关于门禁本身的发现，已记在 MAIN 侧并转 B4**：既然文本扫描无法认可事后修复，那么"57 条违规"**不能**当作"schema 不合规"的代理指标——B2 的纠正器把线上 schema 改合规了，而这个数字一条都没动。B4 被要求把这条限度写在数字被报出的地方，并裁定一个更实际的问题：两个 scope 对**检查哪些时间列**口径不同（catalog 查全部时间列，migrations 只查 `created_at`/`updated_at`），若标准要求的是"全部"，那 migration scope 就是**漏检**——一个漏检的门禁比没有门禁更糟，因为它看上去像覆盖。

#### 12.13.4 `published_only`：诊断接受，改默认值**驳回**

容器端到端报出"教师建题后列表看不到自己刚建的题"。B3 在自有实例上用真实 JWT 复现并定位到根因，**不是 B3 的缺陷、不是 tags、不是课程授权**：

```text
POST /questions                                   -> 201
GET  /questions                                   -> 200 items=0
GET  /questions?published_only=false              -> 200 items=1   ← 包含刚建的那道
GET  /questions?course_id=…&status=draft          -> 200 items=0   ← 参数被静默忽略
```

`published_only` 的契约默认值是 `true`（`b3.yaml:42`），而新建的题是**草稿**，因此被默认过滤掉；`status` **不是**已声明参数，FastAPI 对未声明的查询键**静默忽略**——这才是"加了 `status=draft` 也没用"看起来莫名其妙的原因。B3 还用同一次运行里的对照排除了 `tags`。

**驳回改默认值，维持 `true`**：这个默认是**安全**的那一个（客户端忘了传参数只会少看见、不会多看见）；域层本来就对非教师强制只读已发布，所以默认值并不是保护学生的那道机制；而 B1 已经用显式的 `published_only=false` 开关在客户端解决了它。真正剩下的问题是**可发现性**，那应当用文档解决而不是改行为。已要求 B3 把两个陷阱都写进文档：**新建的题是草稿、因此不在默认列表里**，以及**未声明的查询参数会被静默忽略**。后一条对下一个人同样是陷阱。

同时确认 B3 已收回并更正了它此前关于 `note`/`info` 的错误表述（在 5 处文档里改成"实现偏离契约"），并承认它此前对文档门禁的"更正"才是错的。它还顺手修掉一个真实缺陷：迁移 head 测试**钉死了字面量**，下一个正当 revision 会让它失败而并不因此多证明任何东西。

### 12.14 CHG-0014 / 0015：终于让两侧**并行**对着同一份冻结契约施工

这两份契约变更自 0.9.0 / 0.10.0 起就已生效，四个端点也一直在契约里，**但两侧都没人实现**：B2 侧三个隔离区/证据包端点加一个质量聚合从未写过，B1 侧也没有任何界面消费它们。B1 在自己的报告里如实把它标成"**已指派但未实现**"，并把四个端点列为未消费——这是它连续多轮以来**唯一一项自己指名、却没有被排进任何批次**的工作。

本轮把它拆成两侧同时开工：

| 侧 | 内容 |
|---|---|
| **B2**（排队在 E16 之后） | 实现四个端点；**八条实现侧验证**逐条写明（CHG-0014 四条、CHG-0015 四条），这些断言自变更生效起**从未执行过** |
| **B1**（本轮启动） | 消费四个端点的界面：隔离区复核台、手动归属、证据包下载、题目质量热力图 |

**这里真正值得注意的是施工方式**：两侧同时开工之所以可行，是因为**契约已经冻结且是唯一权威**——B1 照 `b2.yaml` 生成类型并对着契约夹具写界面，B2 照同一份声明写实现，两边不需要先互相等待。这正是"契约先行"存在的理由，而**这是本项目第一次在一条全新变更上真正用上它**（此前的变更多是"契约先行 → 一侧实现 → 另一侧跟上"的串行）。

同时我把**猜契约的口子堵死**：明确告知 B2，若发现契约真的含糊或有误，**停下上报**而不是自行裁决——因为它与 B1 正在同一份契约上从相反方向施工，各自猜一次就会撞成一处真缺陷。这与 §12.13.1 中 B1 拒绝在两种语义之间赌、以及 CHG-0016/0017 两次裁决是同一套纪律。

**诚实边界（已写进两侧的任务书）**：B1 这一批**无法**对着真实 B2 验证——端点尚未实现，因此它的证据只能到"生成类型 + 契约夹具 + 组件测试"为止，文档必须写明"对着**已声明契约**构建、**未经**真实 B2 验证"，不得因为屏幕能渲染就写成已联调。B2 侧则必须逐条报告八条断言各自跑了什么、结果如何，**不能执行的要说明原因**而不是留白。

### 12.15 截至本轮的"仍未执行"清单（集中一处，防止被顺手说成已通过）

这些验证**分散在多份 CHG、模块文档与阶段记录里**，容易在收尾时被漏掉或被"看起来完成了"带过。集中列出，最后交付时必须逐条回到这里核对。

| # | 未执行的验证 | 原因 / 状态 |
|---|---|---|
| 1 | **`scripts/test_evidence.py --check` 整体为红** | 实测：`B1` 的证据记录在契约 **0.7.0**（`4ad48fbb…`）而仓库已在 **0.12.0**（`32b0fa87…`）——台账按设计"过期即失败"；`B2`/`B3`/`B4`/`B5` **从未记录过**。需在重装后跑 `--all` 重新录制 |
| 2 | `CHG-0011`（出题辅助、评语轨）实现侧断言 | 未执行 |
| 3 | `CHG-0012`（逐用例资源账本、文件证据、确定性解释）实现侧断言 | 未执行 |
| 4 | `CHG-0013`（诊断端点、`revision_note`/`revision_diff`、`tags` 过滤）实现侧断言 | B3 已在模块内验证三条证明（含变异证明）；**MAIN 侧未独立复核** |
| 5 | `CHG-0014` 四条 + `CHG-0015` 四条 | 已排入 B2 队列（本轮），**未执行** |
| 6 | `CHG-0016` 两条（端点真实返回键集合等于 `QuestionPublic`；B1 日志字段为真实值） | B1 已用**变异验证**的回归测试覆盖第二条；B3 侧第一条未执行 |
| 7 | `CHG-0017` 五条（省略返回 200 且用例逐字节不变、省略不读受保护内容、提供则整体替换、`cases: []` 写入前被拒、只改标题不改参考答案） | 已派发，**未执行** |
| 8 | `E16` 两模块的"**纠正器在全新库上是真 no-op**"证明 | 在途；要求先 `upgrade` 到纠正器之前断言已合规，再 `upgrade head` 断言无变化 |
| 9 | **浏览器端到端**（真实端口、真实 JWT、走 B1 界面） | 未执行；需 host 重启与 worker |
| 10 | **真实端口 / 跨机 E2E** | 未执行 |
| 11 | **Linux 专属沙箱验证**（nsjail 真跑一次评测） | 本机为 Windows，只能返回 `infrastructure_error`；**这使 `b3.event_inbox` 无法被驱动** |
| 12 | `E11` / `RQ-B-3-30` 验收 5 | **未达标**（窗口路径 23.8 s），且**不得**改写为达标 |

**关于这张表本身的一条纪律**：第 5 项与第 7 项是**新派发**的，不等于已完成；第 4 项与第 6 项是"模块内已验、MAIN 未独立复核"，二者**不是同一件事**，不得互相替代。表中任何一项若在最终交付时仍无法执行，必须在报告里写明原因，而不是从表里悄悄消失。

### 12.16 我往文档里写进了乱码，13 条门禁一声不响——补上 `check_no_replacement_characters()`

写 §12.15 时，`经` 被写成了 `未` + **三个** U+FFFD。诊断就写在证据里：`经` 是三个 UTF-8 字节（`E7 BB 8F`），而它变成了**三个**替换字符，说明那三个字节被逐字节解码过。同类损坏此前也发生过（BOM），所以问题不是"这次手滑"，而是**门禁看不见这一类**。

查了 `check_document_encoding_and_links()` 才确认：它查的是①能按 UTF-8 解码、②没有 BOM、③没有死链。而 **U+FFFD 本身就是合法 UTF-8**——文件解码"完全成功"，损坏却已成永久记录。`check_contract_parses_strictly()` 同样看不见，因为 U+FFFD 也是合法 YAML。**这不是"门禁漏了一条规则"，是"门禁在最该说话的地方没有词"**：一个被错误编解码过的文件，在原有 13 条门禁眼里全都是健康的。

新增 `check_no_replacement_characters()`，覆盖 `.md`/`.yaml`/`.yml`/`.json`/`.py`/`.ts`/`.tsx`/`.css`/`.sh`/`.ps1`（范围刻意比链接门禁宽：同一个损坏出现在契约描述、schema 标题或源码注释里一样是真的）。定为**硬错误而非警告**的理由是：本项目通篇中文，U+FFFD **不存在任何合法用途**，因此没有"需要保留"的情形——这正是它能被安全当作失败处理的原因。

**变异证明**（分别注入一份 markdown 与一份**契约 YAML**，`finally` 中恢复）：

```text
baseline: PASS
CAUGHT  markdown document: FAIL: MAIN/docs/README.md:188: U+FFFD replacement character present (1 occurrence(s))
CAUGHT  contract YAML file: FAIL: MAIN/contracts/openapi/common.yaml:172: U+FFFD replacement character present (1 occurrence(s))
restored: PASS
```

**新门禁自己崩过一次，而那次崩溃有价值**：它扫到 `B2/.pytest-tmp/` 下 pytest 留下的**符号链接**，Windows 无法 `stat`（`WinError 1920`），`path.is_file()` 直接抛 `OSError`。**一个会在构建产物上崩掉的扫描比一个会跳过它的扫描更糟**——所以既跳过 `.pytest-tmp/`，也把 `is_file()` 包进 `except OSError`。这一条只有真跑才会发现：写的时候确实没想到临时目录里会有 Windows 打不开的链接。

**门禁总数 13 → 14，现为 12/14 绿**（红的仍是那两条安装陈旧，同一根因）。

> 本行记的是当时的读数。门禁总数其后**再次增加**：`phase-02 §3.13` 新增
> `check_shared_vocabularies()`，总数 **14 → 15**；同一轮实测 **13/15**（红的仍是那两类，
> 且原因由"B2 未重装"变为"三个工作包正在改源码"）。此处不改写历史读数，只前向指引。

**顺带复核了簇 C 的"可访问性"**：焦点管理（进入、Escape 关闭、Tab 受限、关闭后归还）、每页恰好一个 `h1`、`aria-sort`（含"可排序但当前未排序列为 `none`"的反向断言）**都有真实测试**，不是只写在文档里。值得一提：我第一次 grep 时用 `Select-Object -First 14` **截断了输出**，因而误以为 `aria-sort` 没有测试，复核后确认有——**截断的输出会制造不存在的缺口**，差点被我当成发现报出去。

---

## 13. 容器交付路径：从"起不来"到空卷自举（审计项 E1，第十四轮）

审计把容器路径列为"最严重的一条"：`docker compose up` 是 README 广告的部署方式，但它**起不来**，唯一能跑的是 Windows 脚本 + 一个被 gitignore 的 `.env.integration`——交付物交不出去。本节记录逐条修复与**空卷首次启动**的实测。

### 13.1 缺陷与处置

| # | 缺陷（审计原文） | 处置 |
|---|---|---|
| E1-a | `host/Dockerfile` 从不 `COPY MAIN/contracts`，而 `B2/src/autograder_b2/contracts.py` 运行期需要它 | 镜像内 `COPY MAIN/contracts /app/MAIN/contracts`；`B4_MODULE_ROOT=/app` 让注册表与迁移 head 探针找到它。**同一处还漏了三份 `B1` 之外的锁**：B4 的 `validate_registry` 会校验每个 `B*/contracts.lock` 与 `module.yaml`、以及**实时契约摘要**，因此 `B1/module.yaml` 与 `B1/contracts.lock` 也必须进镜像——只拷 `dist` 会在 `registry.validated` 处直接启动失败 |
| E1-b | `.dockerignore` 排除 `**/dist`，而 Dockerfile 拷 `B1` **源码**却不构建 → `/app/B1/dist` 必缺 | 改为**多阶段构建**：`node:24-slim` 阶段 `npm install && npm run build`（即 `B1/package.json` 自己声明的 `tsc -b && vite build`），`python:3.12-slim` 阶段 `COPY --from=b1 /build/B1/dist`。镜像不再依赖任何已提交的构建产物。用 `npm` 而非 `pnpm`：`pnpm` 在本环境的 runner 下走管道 stdio，Node 的 `child_process` 撞 `EPERM`。`**/dist` 的排除**保留**——它现在排除的是"误入构建上下文的产物"，而不是构建所需的输入 |
| E1-c | compose 只注入平台**超级用户** DSN → B4 按设计拒绝启动；即便启动，三个模块也会塌缩到同一个连接身份，ADR-0001 的逐模块最小权限保证失效 | compose 按 **`<MODULE>_` 前缀**注入四个最小权限 DSN（`B2_DATABASE_URL` / `B3_DATABASE_URL` / `B3_DATABASE_PROTECTED_URL` / `B4_DATABASE_URL`）+ `NATS_URL`；宿主按 ADR-0008 把它们在各模块自己的设置视图里还原成无前缀名。唯一的无前缀 `DATABASE_URL` 改名为 `PLATFORM_DATABASE_URL`，**只**出现在 entrypoint 的迁移与 `ops.provision` 步骤里 |
| E1-d | 缺 `JWT_PRIVATE_KEY_FILE` / `B4_MODULE_ROOT` / `B1_DIST_DIR` | 新增 `entrypoint.sh`：首次启动调 `python -m autograder_b4.ops.generate_key` 写入挂载卷（已存在则跳过），再由同一把密钥导出 B2 需要的 JWKS 文件；`B4_MODULE_ROOT=/app`、`B1_DIST_DIR=/app/B1/dist`。密钥是**卷里的数据**，不是镜像内容——换密钥会让所有已签发令牌失效，而把私钥烤进镜像层同样不可接受 |
| E1-e | 没有任何东西跑迁移 → 新卷必然不可用 | `entrypoint.sh` 定义并执行完整序列：等 PostgreSQL → 生成密钥与 JWKS → `alembic upgrade head`（b2 → b4 → b3）→ `python -m autograder_b4.ops.provision` → `exec uvicorn`。全程用 B4 自己的运维命令，没有手写 SQL |
| E1-f | `.env.example` 里的角色 `autograder_main` **不存在**，端口 5432/4222 与 compose 发布的 55432/54222 不符 | 按现实重写：角色名取自 `infra/postgres/init.sql`，端口与 compose 默认值一致，并写明 compose **不读**这个文件。同时给三个宿主端口加 `POSTGRES_PORT`/`NATS_PORT`/`HOST_PORT` 参数，使同一台机器上跑第二套实例（本节验证正是如此）不必改文件 |
| E1-g | `host` 服务没有 healthcheck，`docker compose up` 报 "running" 而应用可能已死 | 对 `/health/ready` 加 healthcheck：进程活着但某插件不健康时同样判为不健康——"running 但什么也不服务"正是这条检查要暴露的故障 |

**过程中新发现并修复的一处缺陷（不在审计清单里）**：B3 读的公钥集变量是 **`B4_JWKS_URL`**（提供方命名），最初写成 `B3_JWKS_URL` 时，宿主会把它在 B3 的视图里重暴露为 `JWKS_URL`——**没有任何代码读这个名字**，于是 `token_verifier=None`、`/health/ready` 却仍然 `healthy: true`，而受认证路由返回 401 `token verification is not configured`。已改为无前缀 `B4_JWKS_URL`（与 `.env.integration` 一致），并写进 `runbooks/container-deployment.md` §3。

### 13.2 空卷首次启动实测（真实命令与读数）

验证在**独立 compose project** 上进行（`-p agverify`，host `28000`、PostgreSQL `65432`、NATS `64222`），正在服务的参考栈（`main-*` / `b2-*` / `b3-*` / `b4-*`、host `18000`）全程未被触碰。

```powershell
$env:POSTGRES_PORT="65432"; $env:NATS_PORT="64222"; $env:HOST_PORT="28000"
docker compose -p agverify -f MAIN/compose.yaml down -v      # 连卷删除 = 真正的首次启动
docker compose -p agverify -f MAIN/compose.yaml build        # exit 0
docker compose -p agverify -f MAIN/compose.yaml up -d        # exit 0
```

镜像与构建：`2028 modules transformed`、`✓ built in 21.08s`（B1 阶段），Python 阶段 `Successfully installed … autograder-b2/b3/b4/host/platform 0.1.0`，镜像 **395 MB**（磁盘账本为"镜像与工具链 ≤1.5 GB"，仍在预算内）。

首次启动的 entrypoint 输出：

```text
[entrypoint] waiting for PostgreSQL (timeout 120s)
postgres: accepting connections (database autograder)
[entrypoint] generating the B4 Ed25519 signing key
wrote /var/lib/autograder/jwt/jwt-ed25519.pem (kid=2EWpWxp47wvX6Fldgouv_1sTqvoc1klh_Y-ZEmiggK0)
[entrypoint] JWKS written to /var/lib/autograder/jwks/jwks.json
[entrypoint] migrating B2
[entrypoint] migrating B4
{"message": "ops.migration.applied", "from_revision": null, "result_revision": "20260915_0002", "duration_ms": 337}
[entrypoint] migrating B3
[entrypoint] provisioning the B4 least-privilege role
provisioned role autograder_b4 on schema b4: 16 steps
role autograder_b4 is least privilege: no b2/b3 access, no superuser attributes
[entrypoint] starting the composition host on 0.0.0.0:8000
{"event": "registry.validated", "manifests": ["B1", "B2", "B3", "B4"], "loaded": ["B2", "B3", "B4"]}
```

`docker compose ps`：`host Up (healthy)`、`postgres Up (healthy)`、`nats Up`——healthcheck 由"running"变为"healthy"本身就是 E1-g 生效的证据。

`curl http://127.0.0.1:28000/health/ready`：

```json
{"ready":true,"modules":[
 {"name":"B4","healthy":true,"detail":"postgres: postgres reachable; migration: migration head 20260915_0002 matches module.yaml; nats: nats is reachable at nats:4222"},
 {"name":"B3","healthy":true,"detail":"question bank ready; token_verifier=JwksTokenVerifier publisher=NatsEventPublisher service_token_configured=True; …"},
 {"name":"B2","healthy":true,"detail":"http-intake: ok - submission intake is available; email-intake: not-configured …; evaluation-chain: ok …; message-broker: ok …（未发布 0 条，DLQ 0 条）"}]}
```

**三个插件全部 `healthy: true`**，且 B3 的 `token_verifier=JwksTokenVerifier`（§13.1 末尾那处缺陷已修）。

SPA 与深链：

```text
GET /app/       -> 200   <script type="module" crossorigin src="/app/assets/index-yKH0iwAc.js">
GET /app/login  -> 200   <link rel="stylesheet" crossorigin href="/app/assets/index-gPDmobVK.css">
GET /app/assets/index-yKH0iwAc.js -> 200
```

浏览器（Playwright）实测：`/app/` 渲染「AutoGrader 教学评测平台 · 契约 0.10.0」；`/app/login` 用 `container.teacher` 登录成功后跳回首页，导航按角色出现「名单 / 名单导入」，顶栏显示 `当前身份：container.teacher · 教师 · 契约 0.10.0`。控制台唯一的错误是未登录时 `auth/session/refresh` 的 401，属预期行为。

端到端（**真实数据，不只是 health**）：

```text
GET  /api/v1/modules                              -> 200
    B2: contract=0.10.0 routes=['/api/v1/submissions','/api/v1/evaluation-runs','/api/v1/grades','/api/v1/intake'] healthy=True
    B3: contract=0.10.0 routes=['/api/v1/questions','/api/v1/question-versions','/api/v1/assignments'] healthy=True
    B4: contract=0.10.0 routes=[…8 条…] healthy=True

POST /api/v1/auth/session {"username":"admin",…}  -> 200  (token 477 字符)
POST /api/v1/users                                -> 201  user_id=7581d404-…
POST /api/v1/auth/session (teacher)               -> 200  scope=[]
POST /api/v1/courses                              -> 201  course_id=009e0471-db22-4b67-a2d4-e1fcfe00c2b2
POST /api/v1/auth/session (teacher，课程授予之后)  -> 200  scope=[{"course_id":"009e0471-…"}]
POST /api/v1/questions?course_id=009e0471-…       -> 201  question_id=8bf886b6-92a0-47ed-b19c-99f000d08ae7
GET  /api/v1/questions/8bf886b6-…                 -> 200  {"title":"容器验收题：读一行并回显","language":"python","status":"draft",…}
GET  /api/v1/courses                              -> 200  {"items":[{"course_code":"CONTAINER-ACCEPT-2026","title":"容器交付验收课程"}]}
```

即：容器内的 host **签发了令牌、接受并读回了真实业务数据**，而不是只回答 health。注意令牌是**签发时刻的作用域快照**——建课程之前登录得到的 `scope` 为空，必须重新登录才能拿到课程作用域；这不是缺陷，但足以让一个"先登录再建课程"的脚本误判成 403（本轮据此修掉了验收脚本自身的一处顺序错误）。

**资源读数**（`docker stats --no-stream`）：host `113.4 MiB / 160 MiB`、PostgreSQL `53.3 MiB / 96 MiB`、NATS `4.3 MiB / 32 MiB`，合计 **171 MiB**，在 512 MB 账本之内，且仍有 ≥128 MB 余量。这是**读数，不是完整资源门禁**：Linux 冷启动、磁盘与 nsjail 作业仍未测（§6）。

### 13.3 未能测通的一项 —— **已修复（同日，见下）**

`MAIN/scripts/seed_integration.py` 的**发布**一步在容器栈上返回
`422 reference-attestation-missing`（"publish rejected: no reference-answer attestation matches this content"）。
这是该脚本与 B3 当前发布门禁（`RQ-B-3` 的参考解答存证要求）之间的**既有不一致**，与容器路径无关——脚本没有被更新去执行 `scripts/verify_question_bank.py` 并提交存证。因此本轮的端到端证据到**建题（201）+ 按 id 读回（200）+ 课程列表（200）**为止，**不含** publish / assignment 一步。容器交付路径本身不依赖该脚本：`docker compose up` 起来的是空库，播种是可选的管理员操作。

**已修复（MAIN 侧）**：容器代理从一条独立的验证路径重新发现了**我在另一个脚本上已经修过、却没有传播到兄弟脚本**的同一类缺陷。`import_question_bank.py` 早就走 **verify → attest → publish**（执行参考答案 → 提交存证 → 发布），而 `seed_integration.py` 仍然直接 POST `publish`，于是必然撞上 `CHG-0008` 的存证门禁。修法不是把那段序列再抄一遍——**同一段与安全相关的序列有两份拷贝，就是两份会各自漂移的拷贝**——而是直接复用：`seed_integration.imported.attest_and_publish(base, teacher, version_id, payload)`，走全项目**唯一**执行参考答案的那条路径（执行者与记录者刻意分离，B3 不执行任何东西）。**待执行的验证**：完整播种仍未重跑（会向在线环境写入重复课程与题目，不安全），但已单独验证该脚本自己的那道题通过 `verify_cases` → `total=2 passed=2 failures=[]`，即修复后的路径不会再在第一步被拒。**这不等于"发布已测通"**：attest 与 publish 两步的 HTTP 结果仍未实测，必须在最终集成时补齐。

**另一个只在容器路径上才看得见的干扰源**：镜像构建会**快照** `MAIN/contracts`，而 B4 的注册表在启动时把**镜像内**的锁与**镜像内**的契约树比对。本轮验证期间契约正被并发推进（0.1.0 → … → 0.11.0），其间的一次启动以

```text
RegistryError: B1: contract digest drift -- contracts.lock records 4ad48fbb… but the live contract tree hashes to a748b2b7…
```

退出。**这是注册表做对了事**：它比对的是镜像内的两份快照，而当时的构建恰好跨在"契约已改、锁还没跟上"的窗口上，不一致就不许启动。等锁与实时摘要重新一致后重建镜像，启动即通过。记录在此以免后人把这类失败误读成容器路径的缺陷——**正确的处置是重跑 `scripts/relock_contracts.py` 并重建镜像，绝不是想办法绕过这个校验**。注意这一条与 §12.10 的 CHG-0016 是同一现象的两个面：契约版本这次又前进到了 **0.11.0**，因此**任何在此之前构建的镜像都必须重建**。
