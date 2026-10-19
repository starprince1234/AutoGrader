# B1 架构总览

## 在整体架构中的位置（上下文级）

B1 是纯前端静态客户端，由 Vercel 从 B1 目录构建，并在 www.91idc.me 的 /app/ 路径提供。
浏览器通过 HTTPS 直接调用 api.91idc.me；Cloudflare Tunnel 将 API 请求送到 Linux systemd host 的 loopback。

~~~text
浏览器 (B1 static UI, React 19)
   │ HTTPS fetch + SSE (credentials include, API origin: https://api.91idc.me)
   ▼
Cloudflare edge → encrypted Tunnel → cloudflared → MAIN composition host (127.0.0.1:18000)
   ├── B4 身份/课程/名单插件
   ├── B3 题库插件
   └── B2 评测插件与 SSE
~~~

B1 从不导入 B2/B3/B4 的代码、ORM 或 schema；交互只经版本化 HTTP/SSE 契约。
## 内部层次与依赖方向

依赖方向自上而下，上层可依赖下层，下层不得反向依赖：

```text
presentation（页面/路由/角色守卫，@tanstack/react-router）
      │
      ▼
application（React Query hooks：查询缓存、提交编排、SSE 订阅）
      │
      ▼
infrastructure（生成的 API 客户端 + SSE 客户端 + 本地草稿存储 + 前端日志）
      │
      ▼
generated types（由 MAIN OpenAPI 生成，见 ADR-0002）
```

- `presentation` 只调用 `application` 层暴露的 hook，不直接发 fetch。
- `infrastructure` 是唯一直接接触 `fetch`/`EventSource`/浏览器存储的层。
- `generated types` 是被依赖的类型底座，任何层都不得手写契约字段类型。

## 主要数据流

```text
1. 认证：B1 → POST /api/v1/auth/session（B4）→ 会话凭据（刷新凭据仅 HttpOnly cookie）
2. 名单：B1 → POST/GET /api/v1/roster-imports(+confirm)（B4）→ 预览 → 确认
3. 题目：B1 → GET /api/v1/questions、/question-versions、/assignments（B3）→ 渲染题干与图片
4. 提交：B1 → POST /api/v1/submissions（B2）→ submission_id
5. 进度：B1 → GET /api/v1/evaluation-runs/{id}/events（SSE）→ 实时状态（去重）
6. 成绩/导出：B1 → B2 成绩查询/导出端点 → 展示/下载
```

## 与其他模块的交互方式

| 邻居 | 交互方式 | 说明 |
|---|---|---|
| MAIN host | HTTPS API（经 Cloudflare Tunnel） | 提供 B2/B3/B4 契约 API；origin 只监听 loopback |
| B4 | HTTPS（API origin） | 身份/课程/名单，**无事件** |
| B3 | HTTPS（API origin） | 题目/版本/资源，**无事件** |
| B2 | HTTPS + SSE（API origin） | 提交/评测/成绩/导出；进度经 SSE |

**关键立场**：B1 从不导入它们的代码；一切交互都经契约。前端隐藏按钮/路由守卫**不是**安全边界（见 `boundaries.md`）。
