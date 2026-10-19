# AutoGrader 架构总览

本文回答四个问题：**系统长什么样、模块怎么划分、数据怎么流、出错时怎么办。** 决策理由见 `../adr/`。

---

## 1. 系统上下文（谁和谁说话）

```text
                  ┌──────────────────────────────────────────────┐
   ┌──────────┐   │            AutoGrader Beta                   │
   │  学生     │   │  （B1 在 Vercel，API 在 Linux host）          │
   │  浏览器   │◀─▶│                                              │
   └──────────┘   │  HTTPS /app + HTTPS /api/v1                             │
   ┌──────────┐   │                                              │
   │  教师/助教│◀─▶│                                              │
   │  浏览器   │   │                                              │
   └──────────┘   │                                              │
   ┌──────────┐   │                                              │
   │  管理员   │◀─▶│                                              │
   │  浏览器   │   │                                              │
   └──────────┘   └───┬────────────────┬─────────────────┬───────┘
                      │                │                 │
              ┌───────▼──────┐  ┌──────▼───────┐  ┌──────▼─────────┐
              │  IMAP 邮箱    │  │ PostgreSQL 17 │  │  宿主 Linux     │
              │ （收件入口）   │  │ （单实例多schema）│  │ cgroup v2 /    │
              └──────────────┘  └──────────────┘  │ nsjail / seccomp│
                                                 └────────────────┘
```

**外部边界说明**

| 边界 | 方向 | 说明 |
|---|---|---|
| 浏览器 → 系统 | 入 | Vercel 的 www.91idc.me 跨源访问 api.91idc.me；API 精确允许生产 origin，刷新凭据走 HttpOnly cookie |
| Cloudflare → API | 入 | Cloudflare Tunnel 把 `api.91idc.me` 转发到服务器 loopback 的 API；无需开放公网 API 端口 |
| IMAP 邮箱 → 系统 | 入 | B2 主动拉取；无法唯一匹配的邮件进入**隔离**，不自动评分 |
| 系统 → 宿主内核 | 出 | 仅 B2 使用 nsjail/cgroup v2/seccomp 执行沙箱。**无沙箱能力则拒绝执行动态评测**（ADR-0004） |
| 系统 → 外部网络 | **无** | 运行期不调用任何外部服务；**沙箱内禁网**。这也是评分不用 LLM 的副作用（ADR-0005） |

> **注意**：系统**没有**"调用大模型服务"这条边界。这是刻意设计，不是遗漏。

---

## 2. 生产部署视图（运行期进程/组件）

~~~text
Vercel: www.91idc.me/app
        │ HTTPS fetch + SSE, credentials include
        ▼
Cloudflare edge: HTTPS api.91idc.me
        │ encrypted Cloudflare Tunnel
        ▼
cloudflared connector → http://127.0.0.1:18000
        ▼
systemd native API service (non-root, 127.0.0.1:18000)
        ├── B2 / B3 / B4 plugins
        ├── B2 child process → nsjail → student code
        ├── PostgreSQL 17 (Docker, loopback only)
        └── NATS JetStream (Docker, loopback only)
~~~

| 组件 | 形态 | 资源预算 | 说明 |
|---|---|---|---|
| B1 | Vercel static deployment | CDN | Vite output stays under /app/; builds only when B1 files change |
| composition host | Native systemd service | ≤160 MB / 0.30 vCPU | Loads B2/B3/B4 plugins; localhost HTTP behind Cloudflare Tunnel |
| PostgreSQL 17 | Docker container | ≤96 MB / 0.15 vCPU | One instance, three schemas, least-privilege roles |
| NATS JetStream | Docker container | ≤32 MB / 0.05 vCPU | Event bus; persistent data ≤0.25 GB |
| nsjail case | Child process in delegated cgroup v2 | ≤96 MB / 0.45 vCPU | One concurrent evaluation; fail closed if unavailable |
| cloudflared | Native systemd service | ≤32 MB / 0.05 vCPU | Outbound-only tunnel connector; API origin remains loopback-only |

The API systemd service delegates cpu, memory, and pids controllers to nsjail. The delegated cgroup subtree is exposed inside the service mount namespace at /sys/fs/cgroup, so the application stays non-root and does not need a privileged container. Cloudflared opens only outbound connections to Cloudflare; the server does not expose the API, PostgreSQL, or NATS ports publicly.

## 3. 组件视图（模块职责与依赖方向）

```text
                          ┌───────────────────────┐
                          │   B1  Web 客户端        │
                          │   （静态 React 产物）    │
                          │   无权威数据            │
                          └───┬───────┬───────┬───┘
                              │       │       │
        会话/课程/名单 ◀───────┘       │       └────────▶ 题目/版本
                                      │
                                      ▼
                          ┌───────────────────────┐
                          │   B2  评测引擎          │
                          │   提交 · 静态 · 安全 ·   │
                          │   沙箱执行 · 证据 · 分数 │
                          │   ← 分数与证据的唯一权威  │
                          └───┬───────────────┬───┘
                              │               │
              拉取不可变包 ◀───┘               └──▶ 发布 evaluation.* 事件
                              │
                              ▼
                          ┌───────────────────────┐
                          │   B3  题库              │
                          │   题干 · 参考答案 ·      │
                          │   测试包 · 去标识化统计   │
                          │   不执行代码、不算分      │
                          └───────────────────────┘

                          ┌───────────────────────┐
                          │   B4  平台底座          │
                          │   身份 · 课程 · 名单 ·    │
                          │   DB/NATS 运维 · 注册表  │
                          └───────────────────────┘
                            （所有模块依赖其能力，
                              但只通过 JWT 本地校验，
                              不做同步回调）
```

### 依赖方向规则

1. **依赖只允许单向且通过契约**：B1 → B4/B3/B2；B2 → B3（拉包）；B2 → B4（验签）。**没有反向调用**。
2. **B4 被所有人依赖，但无人同步调用它**：其他模块本地验签 JWT，不回调 B4 的 `/api/v1/auth/*`。这是为了避免把认证变成全局串行热点与单点。
3. **禁止跨模块 import**：B2 不得 `import autograder_b3`。契约是唯一的连接面。
4. **禁止读对方表**：由数据库权限物理阻止（每模块一个 role，仅本 schema）。

### 模块职责一句话

| 模块 | 一句话 | 拥有 | 永不拥有 |
|---|---|---|---|
| **B1** | 学生/教师/管理员的 Web 界面 | 展示与交互、未提交草稿 | 任何权威业务数据 |
| **B2** | 提交接收与评测引擎 | 提交物、静态/安全结论、执行证据、分数、报告、导出 | 题目、身份 |
| **B3** | 题库与测试包 | 题干、参考答案、不可变版本、测试包、去标识化统计 | 执行、分数、证据 |
| **B4** | 平台底座 | 身份、课程、选课、审计、DB/NATS、运行期注册表 | B2/B3 业务表、契约作者身份 |

---

## 4. 数据流（四条主链）

### 4.1 提交与评测（核心链路）

```text
B1 ──POST /api/v1/submissions──▶ B2
                                  │ 1. 验签 + 课程作用域
                                  │ 2. 信封 schema 校验 + 逐文件摘要核对
                                  │ 3. 幂等键判重（命中→返回既有 submission_id）
                                  │ 4. 内容寻址存储（SHA-256 去重）
                                  │ 5. 状态 validated → queued
                                  │ 6. outbox 写 submission.accepted
                                  ▼
                              B2 拉取 B3 的 evaluation package（不可变 + 摘要）
                                  │ 7. 静态合法性检查（AST / bash -n / 规则）
                                  │ 8. 安全性判定  ──高危──▶ rejected（不进沙箱）
                                  ▼
                              nsjail 逐用例执行（禁网/只读根/限额）
                                  │ 9. 逐用例证据（实际 vs 期望 + 失败类别）
                                 ▼
                              评分为确定性计算 + 合计校验
                                  │10. 持久化 run/result/evidence
                                  │11. 生成报告（总评 + 细评）
                                  │12. outbox 写 evaluation.completed（去标识化）
                                  ▼
B1 ◀──SSE /events── 进度与终态        B3 ◀──订阅──▶ 统计聚合（无源码）
```

### 4.2 名单导入

```text
教师 ──上传 PDF/XLSX──▶ B1 ──POST /api/v1/roster-imports──▶ B4
                                                              │ 解析（不确定→报错，不猜）
                                                              │ 列映射（无法唯一映射→要求人工）
                                                              │ 逐行校验（错误行全部保留）
                                                              │ 预览（**数据库零写入**）
                          B1 ◀──预览── B4 ◀──人工确认──────────┘
                                            │ 幂等 + 原子写入
                                            └─▶ outbox: roster.import.completed
```

### 4.3 题目发布

```text
教师 ──▶ B3 创建/编辑草稿 ──publish(校验)──▶ published（不可变）
                                              │ 绑定内容摘要
                                              └─▶ event: question.version.published
                                                        │
                                                        ▼
                                              B2 感知可用版本（缓存失效）
```

### 4.4 认证

```text
用户 ──POST /api/v1/auth/session──▶ B4 签发 Ed25519 短期 JWT + HttpOnly 刷新 cookie
                                          │ 暴露 JWKS 公钥
                                          ▼
      B1 携带 Bearer ──▶ host ──▶ B2/B3/B4 各自**本地验签**（不回调 B4）
```

---

## 5. 生命周期与失败处理

### 5.1 启动

```text
1. 读取配置（环境变量）
2. 连接 PostgreSQL / NATS  ——（失败 → readiness 失败并指明依赖；不伪装健康）
3. 发现 entry point: autograder.modules
4. 解析 capability DAG  ——（缺能力/循环 → **启动失败**，报出缺失项）
5. 校验：重复路由、重复 schema 归属、契约摘要漂移  ——（任一 → **启动失败**）
6. 按序 include_router
7. 暴露 /health/live 与 /health/ready
```

**失败即失败**：不降级、不跳过、不用默认值掩盖。启动失败的信息必须指明**具体模块与原因**。

### 5.2 依赖故障矩阵

| 故障 | 影响面 | 系统行为 | 明确不做 |
|---|---|---|---|
| PostgreSQL 不可用 | 所有 API 操作 | readiness 失败并指明 `postgres`；写操作返回错误 | 不用内存/文件替代；不返回假成功 |
| NATS 不可用 | 事件与统计 | readiness 失败并指明 `nats`；同步提交路径**仍可用**；事件滞留 outbox | 不回退为同步跨模块调用；不丢事件 |
| nsjail/cgroup 不可用 | 动态评测 | 拒绝执行动态评测并报告阻塞（`failed` 并说明原因） | **绝不**退化为无沙箱执行 |
| B3 package 不可用 | 评测 | 结算为 `failed`（技术原因），不是学生低分 | 不伪造用例通过 |
| 单用例超时 | 该用例 | 清理整个进程树；该用例判失败；其余用例继续 | 不跳过、不伪造通过 |
| 插件导入期抛异常 | host 稳定 | 捕获并反映为 readiness 失败 | 不让 host 进程退出 |
| Cloudflare Tunnel 未连接 | 外部 API | `api.91idc.me` 不可达；本机 API 与 readiness 可独立检查 | 不将 API 绑定到公网接口 |

### 5.3 关闭

优雅关闭：停止接受新请求 → 等待在途请求 → 冲洗 outbox → 清理沙箱工作目录与残留进程。**沙箱残留进程必须在关闭路径上被清理**，否则会污染下一次启动。

---

## 6. 契约与一致性机制

```text
MAIN/contracts/**  ──sha256（含路径+字节）──▶  digest
                                                 │
                    ┌────────────────────────────┼────────────────────────────┐
                    ▼                            ▼                            ▼
            B1/contracts.lock            B2/contracts.lock            B3/… B4/…
                    └────────────┬───────────────┴────────────────────────────┘
                                 ▼
                    host checks：四份锁的版本与摘要必须等于实时摘要
```

- 契约权威：OpenAPI 3.1 / AsyncAPI 3.0 / JSON Schema 2020-12。
- 版本初始 `0.1.0`；变更分级与流程见 `../contracts/change-protocol.md`。
- **摘要不一致 ⇒ 集成失败**。这是防止"某人偷改契约"的机制，不是形式主义。

## 7. 可观测性

| 手段 | 内容 |
|---|---|
| 结构化日志 | JSON 行，含 `timestamp`、`level`、`event`、`producer`、`request_id`；有上下文时含 `correlation_id` 与业务 ID |
| 关联 | W3C `traceparent` + `X-Request-ID` 贯穿请求、日志、事件（事件中为 `correlation_id`/`causation_id`） |
| 健康 | `/health/live`（进程活着）、`/health/ready`（依赖真实可用 + 各模块 health 报告） |
| 注册表 | B4 提供运行期模块清单（能力/路由/事件/schema 归属/迁移 head），使"即插即用"可观测（ADR-0003） |
| 审计 | 认证、权限拒绝、管理操作、名单导入、题目发布、评测触发 |
| 脱敏 | 日志/事件/导出中禁止出现源码、参考答案、隐藏用例、凭据、内部绝对路径、超范围 PII |

---

## 8. 技术栈与选型理由（摘要）

| 层 | 选择 | 理由 | 被否方案 |
|---|---|---|---|
| 后端语言 | Python 3.12 | 课程规定的被评测语言之一；生态成熟 | — |
| Web 框架 | FastAPI | Pydantic v2 原生、OpenAPI 3.1 自动生成、异步 SSE 支持好 | Flask（无原生 schema/OpenAPI）、Django（过重） |
| 数据校验 | Pydantic v2 | 与 FastAPI 同源；契约即模型 | 手写校验 |
| ORM | SQLAlchemy 2 | 与 Alembic 同源；显式事务控制 | 裸 SQL（易注入、难迁移） |
| 迁移 | Alembic | 每模块独立版本表，满足 schema 隔离 | 手工 DDL（不可审计） |
| 数据库 | PostgreSQL 17 | schema 级权限隔离、`timestamptz`、部分唯一索引 | SQLite（无 schema 隔离与真并发）、MySQL（权限模型较弱） |
| 消息 | NATS JetStream | 常驻内存小、原生持久化与 durable consumer | Kafka/RabbitMQ（内存远超预算，见 ADR-0006） |
| 沙箱 | nsjail + cgroup v2 + seccomp | 无 daemon、声明式、原生资源限制 | Docker（需常驻 daemon）、Firejail（cgroup 集成较弱），见 ADR-0004 |
| 前端 | React 19 + TS + Vite | Vercel static deployment、无自有服务端（ADR-0017） | Next.js SSR（额外常驻进程） |
| 编辑器 | Monaco | 与 VS Code 同源，Python/Bash 高亮成熟 | CodeMirror（可选，但 Monaco 更贴近教学场景） |
| 图表 | ECharts | 成绩统计与分布展示 | 自绘 SVG（成本高） |
| 包管理 | uv / pnpm | 锁文件可复现、速度快 | pip-tools/npm（较慢且锁不严） |

---

## 9. 已知架构缺口（如实记录）

| 缺口 | 影响 | 记录位置 | 计划 |
|---|---|---|---|
| `B1/module.yaml` 的 `entry_point: static:dist` 不被 host 消费；B1 不在 capability DAG 中 | B1 缺失时 host 不会失败 | ADR-0017 | Vercel 独立发布静态客户端，不纳入后端插件装载 |
| `ModuleDescriptor.events` 是扁平元组，不区分 publishes/subscribes | 无法校验事件方向声明 | ADR-0003 | 需 P3 级 SDK 变更 |
| capability 名称无集中登记表 | 拼写漂移风险（靠装载失败兜底） | ADR-0001 | 功能阶段 |
| 单进程插件无隔离，插件崩溃可能影响 host | 可用性风险 | ADR-0001 | 插件导入期禁止重 I/O；异常捕获并转为 readiness 失败 |
| 单实例 PostgreSQL 是单点 | 无高可用 | ADR-0001、PRD 非目标 | 本期不做 |
| 评测结果保留期无具体默认值 | 磁盘账本不确定 | ADR-0004 相关、SRS 6.2 | B2 功能阶段定值 |
| 反作弊（硬编码输出）未覆盖 | 学术诚信风险 | ADR-0005 遗留风险 | 需课程方确认是否属范围 |
