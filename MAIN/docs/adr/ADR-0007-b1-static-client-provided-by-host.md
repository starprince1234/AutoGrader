# ADR 0007 B1 采用静态客户端形态，由 composition host 同源提供

- **状态**：接受
- **背景**：

  导图（PDF）为 Beta 明确列出"**前后端分离，BS 架构**"，并把 JavaScript/Nodejs 列为开发工具。因此 B1 必须是浏览器端应用，且技术上"前后端分离"。

  但"前后端分离"在工程上有多重实现方式，选择会显著影响内存预算、部署复杂度与跨域处理：

  | 方案 | 是否满足"前后端分离" | 资源开销 | 部署复杂度 | 跨域/凭据 | 结论 |
  |---|---|---|---|---|---|
  | A. B1 自带 Node 服务器（SSR/Next.js 服务） | 是 | **高**（常驻 Node 进程，与 512 MB 账本冲突） | 高（多一个服务） | 需要代理配置 | 否 |
  | B. B1 单独部署为静态站点 + 独立域名/CORS | 是 | 低 | 中（需要 CORS、cookie 跨站策略会变复杂） | **Cookie 跨站会显著复杂化认证安全模型** | 否 |
  | C. **B1 构建为静态资源，由 MAIN host 同源提供** | 是（构建期分离、运行期同源） | 低（静态文件，无额外进程） | 低（一个 host） | **同源，无 CORS，Cookie 策略简单可靠** | **采纳** |
  | D. B1 与后端同仓库单进程 SSR | 否（不算分离） | 低 | 低 | 无 | 否（违反导图要求） |

  关键考量：本系统采用 **HttpOnly + SameSite cookie 存放刷新凭据**（`RQ-B-4-50`）。跨站部署会让 `SameSite` 策略与 CSRF 防护显著复杂化，而本项目没有跨域需求。同源是安全性与简单性的双赢选择。

- **决策**：

  1. **B1 是独立的静态前端项目**：React 19 + TypeScript 5.7 + Vite 7，构建产物为纯静态文件（`dist/`）。
  2. **B1 不实现任何服务端逻辑**，不拥有权威数据，不连接数据库或消息中间件。
  3. **运行期由 MAIN composition host 同源提供**：host 读取环境变量 `B1_DIST_DIR`（容器内默认为 `/app/B1/dist`），挂载到 `/app`，并开启 SPA 回退（`html=True`）。
  4. **B1 通过同源相对路径访问 API**：环境变量 `VITE_API_BASE_URL=/api`。因此**没有 CORS 配置**，刷新凭据使用 `HttpOnly` + `SameSite` cookie 即可。
  5. **B1 的 API 类型由 MAIN 的 OpenAPI 生成**，不手抄字段定义（避免契约漂移）。
  6. **B1 的入口声明为非 HTTP 路由**：`module.yaml` 中 `entry_point: static:dist`、`routes: []`、`provides: [web-ui]`。它不参与后端路由注册，因此不会与 B2/B3/B4 的路由产生冲突。
  7. **B1 允许本地持久化的范围**（因无权威数据库）：
     - **允许**：未提交的代码草稿、UI 偏好（主题、面板折叠状态）；
     - **禁止**：令牌、刷新凭据、成绩、名单、参考答案、任何权威业务数据的副本。
  8. **"前后端分离"的落地解释**：分离体现在**开发与构建期**（独立项目、独立 Git、独立依赖、独立构建、独立测试、独立的契约消费方式）与**接口边界**（只经版本化 HTTP/SSE 契约通信）上，而不体现为部署期的两个进程。这一解释需在评审时明确说明。

- **后果**：

  - **获得**：不引入额外常驻进程，静态资源几乎不占内存，符合 512 MB 账本。
  - **获得**：同源消除 CORS，使 `SameSite` cookie 的认证模型简单且安全；`SameSite=Strict/Lax` 可直接使用。
  - **获得**：部署只需一个 host 容器；本地开发时 Vite dev server 通过代理指向 host（runbook 覆盖）。
  - **获得**：B1 完全无法访问数据库，从架构上保证"B1 无权威数据"这一边界（不是靠自律）。
  - **付出**：B1 无法做 SSR（首屏 SEO 无关紧要——这是内部教学系统，不需要搜索引擎索引）。
  - **付出**：B1 的构建产物是 host 的运行期输入，因此**集成测试需要先构建 B1**。host 在 `dist` 不存在时**不挂载** `/app`（不报错），这会导致"前端打不开但后端健康"的表现 → 必须在 runbook 中明确记录该现象与排查方法。
  - **付出**：`entry_point: static:dist` 是一个**宿主需要理解的特殊形式**（不是 Python entry point）。当前 host 的实现只处理 `AUTOGRADER_MODULES` 环境变量与 `autograder.modules` entry point（针对 Python 插件），静态客户端的提供是通过 `B1_DIST_DIR` 直接挂载实现的，**不经过插件装载机制**。这意味着 B1 的 `entry_point` 字段目前**不被宿主消费**，仅作为清单声明存在。
  - **遗留风险**：
    - 上面这一"声明与消费不一致"是一个真实缺口（B1 不在 capability DAG 中，因此 host 不会因 B1 缺失而失败）。是否要把静态客户端纳入插件机制，需在功能阶段决定；在此之前必须在文档中如实记录，不得声称 B1 已被 host 的插件机制装载。
    - 若未来需要跨站部署（例如前端放 CDN），本决策需要重新评估：`SameSite` 与 CSRF 策略会变，属于 P3 级变更。

- **关联**：
  - 需求：`RQ-B-1-10`～`RQ-B-1-99`、`RQ-B-0-20`
  - ADR：`ADR-0001`（插件式 host）、`ADR-0002`（需求基线，导图"前后端分离"分支）
  - 契约：`MAIN/contracts/openapi/*.yaml`（B1 消费）
  - 实现：`MAIN/host/src/autograder_host/main.py`（`B1_DIST_DIR` 挂载）、`B1/module.yaml`（`entry_point: static:dist`）、`B1/.env.example`（`VITE_API_BASE_URL`）
  - 文档：`MAIN/docs/architecture/overview.md`、`MAIN/docs/runbooks/local-development.md`
