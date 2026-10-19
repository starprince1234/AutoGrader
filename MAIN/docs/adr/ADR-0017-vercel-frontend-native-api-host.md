# ADR-0017 B1 部署到 Vercel，API 原生运行于 Cloudflare 域名后

- **状态**：接受
- **影响范围**：B1、MAIN、B2、B3、B4、运维
- **关联需求**：RQ-B-0-20、RQ-B-1-10～RQ-B-1-99、RQ-B-2-50/70
- **取代**：[ADR-0007](ADR-0007-b1-static-client-provided-by-host.md)

## 背景

原生产交付路径把 B1 的 dist 构建进 composition host 镜像，并由 host 在 /app 同源提供。
新的部署目标明确要求 B1 使用 Vercel、API 运行在用户的 Linux 服务器，并由 Cloudflare 为 API 提供
api.91idc.me。这会改变静态资源交付、浏览器凭据发送、跨域策略和沙箱进程的运行边界。

评估过的方案：

| 方案 | 结论 | 原因 |
|---|---|---|
| 保留 host 对 /app 的静态挂载 | 否 | 前端仍由后端服务器交付，不符合目标部署拓扑 |
| 把包含 nsjail 的 API 放在普通 Docker 容器中 | 否 | 项目已实测容器内 nsjail 需要扩大权限；不得以 --privileged 绕过隔离边界 |
| B1 单独部署到 Vercel，API 作为原生 systemd 服务 | **采纳** | Vercel 独立构建静态前端；systemd 在 cgroup v2 宿主上委派 CPU、内存和进程控制器给非 root API 进程 |
| API 直接暴露公网端口 | 否 | Cloudflare Tunnel 可提供 HTTPS 与入口隐藏，无需开放 API、数据库或 NATS 端口 |

## 决策

1. B1 的 Vercel 项目根目录为 B1，使用 Vite 输出 dist。应用继续位于 /app/，根路径跳转到 /app/；仅当前端构建输入或 MAIN/contracts/** 变化时构建，文档-only 提交跳过。
2. 生产 API 在 api.91idc.me 提供。Cloudflare Tunnel 将该主机名转发到服务器 127.0.0.1:18000；服务器不开放 API、PostgreSQL 或 NATS 公网端口。
3. composition host 作为非 root systemd 服务运行。systemd 委派 cgroup v2 的 cpu、memory、pids 控制器，并将服务的委派子树映射到 nsjail 约定的 /sys/fs/cgroup。沙箱仍只使用 nsjail；不可用时继续 fail closed。
4. PostgreSQL 与 NATS 仍以容器运行，只绑定 loopback。应用数据目录位于 /var/lib/autograder。
5. B1 请求使用 credentials: include。API 只允许 https://www.91idc.me 这一精确来源；Cookie 仍为 HttpOnly、Secure、SameSite=Strict。两个子域同属 91idc.me，所以它们跨源但同站。
6. Vercel 的 Git 集成负责 B1；GitHub Actions 只在后端实现、迁移、契约或部署文件变化时，通过 Doppler 获取部署凭据并更新 systemd API 服务。
7. `www.91idc.me` 是正式前端入口，`91idc.me` 由 Vercel 308 跳转到 `www`。API 使用 `api.91idc.me`；Cloudflare DNS 保留 Vercel 现有根域和 `www` 记录。

## 后果

- **获得**：前端、API、数据库与评测沙箱各自有明确部署边界；公共请求经 Cloudflare，origin API 只监听 loopback。
- **获得**：评测进程运行在原生 cgroup v2 上，不需给应用容器 --privileged。
- **付出**：首次部署需要 Cloudflare Tunnel 凭据、Vercel 项目设置、Doppler prd 运行变量，以及一个管理员账号。
- **付出**：B1 与 API 使用不同 origin，需要精确 CORS 配置；运行时所有浏览器 API 请求必须带凭据。
- **遗留风险**：systemd 的 cgroup 委派和 mount namespace 映射必须在目标内核上实测；/health/ready 的绿色状态不能代替 Linux nsjail 安全验收。

## 关联

- 实现：B1/vercel.json、B1/src/api/client.ts、MAIN/host/src/autograder_host/main.py
- 部署：MAIN/compose.production.yaml、MAIN/deploy/autograder.service、.github/workflows/deploy-backend.yml
- 验收：MAIN/docs/runbooks/linux-acceptance.md
