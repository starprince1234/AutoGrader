# B4 架构总览

## 系统位置

B4 是 MAIN composition host 装载的一个插件，与 B2/B3 并列，全部同进程由 host 按 entry point 装载。

```text
                    ┌─────────────────────────────────────────────┐
B1 静态 UI ─────────▶│        MAIN composition host（微内核）       │
   HTTP/SSE          │  ┌─────────┐ ┌─────────┐ ┌─────────┐      │
                     │  │   B4    │ │   B3    │ │   B2    │      │
                     │  │ 身份/课程│ │  题库   │ │  评测   │      │
                     │  └────┬────┘ └────┬────┘ └────┬────┘      │
                     └───────┼───────────┼───────────┼───────────┘
                             │           │           │
                     PostgreSQL（单实例多 schema）      NATS JetStream
                       b4 · b3 · b2                    (at-least-once)
```

- B4 是**身份权威**：签发令牌、暴露 JWKS；其他模块**本地验签，不回**调 B4。
- 模块之间**只经版本化 HTTP / 事件契约**交互，**从不导入彼此的代码**、不直读彼此的表。

## 内部层次与依赖方向

```text
router/API 层（FastAPI，路由挂到 /api/v1/auth|courses|roster-imports）
        │ 依赖
        ▼
service 层（认证、课程、名单导入管道、审计、注册表聚合）
        │ 依赖
        ▼
repository 层（仅访问 b4 schema，最小权限角色）
        │ 依赖
        ▼
infra 层（PostgreSQL 连接、NATS JetStream、Ed25519 密钥/JWKS、结构化日志）
```

依赖方向**单向向下**：上层不反向依赖下层实现细节；所有层都不跨 schema、不导入邻居模块代码。各层均已实现
（`src/autograder_b4/`），实现与验证证据见 `../phases/active/phase-01-platform-implementation.md`。

## 主要数据流

1. **认证流**：B1 → `POST /api/v1/auth/session` → B4 校验凭据 → 签发短期 JWT + 设置刷新 cookie → B1 后续请求携带令牌；B2/B3 用 JWKS 本地验签。
2. **名单导入流**：B1 上传名单 → B4 解析→映射→校验→预览→人工确认→原子写入 → 事件入 outbox（同事务）→ 中继发布 `autograder.v1.roster.import.completed`。
3. **注册表流**：host 装载各插件 → B4 启动时校验三方一致（漂移即失败）→ 运行期暴露能力/健康查询。

## 与其他模块的交互方式

| 对象 | 交互方式 | 说明 |
|---|---|---|
| MAIN host | 装载 + settings + health 钩子 | 我不导入 host 代码；host 经 entry point 调用我 |
| B1 | HTTP（我提供的认证/课程/名单端点） | B1 只是客户端 |
| B2 / B3 | 事件 + 本地验签（我签发令牌） | 它们不回调我验签；我从不导入它们代码 |

## 已知缺口（必须保留在文档）

- `descriptor.events` 是**扁平元组**且只列 1 个事件，不区分 `publishes`/`subscribes`（SDK 缺口，ADR-0003 记录）；
  注册表据此只做集合包含校验，方向以 `module.yaml` 为准。
- `MAIN/infra/postgres/init.sql` 只建 schema，**无角色供给**：供给由 B4 自己的
  `ops/provision.py` 承担（不修改 MAIN 的文件）。
- JetStream 配额（0.25 GB）为配置值并对齐资源账本，**实测占用计量待补**。
- 契约侧三处缺口（描述文字与属性名不符、无解析说明字段、无课程级角色授予 operation）见
  `../api/contract.md` 与 `../collaboration/interfaces.md`。
