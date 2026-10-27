# 运行手册：生产部署

生产拓扑：B1 在 Vercel 的 `www.91idc.me/app/`；API 在 Linux 主机上以非 root systemd 服务运行，只监听 `127.0.0.1:18000`；Cloudflare Tunnel 将 `api.91idc.me` 转发到 API。PostgreSQL 与 NATS 只绑定 loopback。

前端 DNS 与 Vercel 自定义域名已按用户交接记录完成。2026-10-19 通过 SSH 实测服务器为 OpenCloudOS 9.6 x86_64、Docker Compose 5.4.0、cgroup v2。首次生产预检当时通过：4 CPU、3655 MiB RAM（当时可用 1602 MiB）、`/var/lib` 所在磁盘总量 39 GiB/可用 15 GiB，首部署项目、卷、服务及目标端口无冲突。第一次部署随后中断，服务器失败后的只读状态记录如下；当前发布流程见 §2。

**当前生产状态（2026-10-20，SSH 直查）**：GitHub Actions run [`39384963992`](https://github.com/starprince1234/AutoGrader/actions/runs/39384963992) 结论为 `failure`。候选 SHA `9b2792e4a4026a86bca754e7f9714a9c34b212d8` 的 release 构建、数据库迁移及 B4 最小权限角色供给已完成；systemd 当前报告 API 与 Tunnel 均为 `inactive/disabled`，`/opt/autograder/current` 不存在。数据库当前 revisions 为 B2 `20260915_0014`、B3 `20260915_0004`、B4 `20260915_0002`。失败 run 的迁移前 dump 为 178401 bytes；2026-10-20 已将其恢复到隔离数据库，确认 B2/B3/B4 三个 schema、53 张模块表与上述 revisions 后删除演练数据库，恢复演练通过，生产数据库未被恢复或删除。

失败 run 后的 systemd journal 确认过旧 release `15b3498dfce29ae1698f903f0450f71d19af17ed-1` 因缺少 `B2/contracts.lock` 启动失败；候选 SHA 的后续启动日志则记录 registry 验证通过并多次返回 HTTP 200 readiness，但 run 最终仍判定本机未 ready。HTTP 200 不等价于 JSON 中 `ready=true`，该候选的健康响应正文和最终模块健康分项尚未取得，不能据此判断根因已解决。服务器部署控制器当前 SHA-256 为 `75657d87fbc5235ca8f4144f2f05f0a37c599be5c374be99780d26c82675a0d8`；与仓库控制器不一致，仍需单独更新并核验。

**本次源码审查结论**：上述失败 release 的健康检查读取 `module`，但 SDK `HealthReport` 实际序列化为 `name`，因此模块集合校验必然失败，即使响应中的 `ready=true`。已在仓库修复并补充接口回归测试；尚不能据此声称生产已恢复。以下流程取代历史 verify run 复用、首部署残留识别与自动回滚流程。

## 1. 首次准备

### 域名与 DNS

1. Cloudflare zone `91idc.me` 已为 Active，阿里云 nameserver 已指向 Cloudflare：`algin.ns.cloudflare.com`、`priscilla.ns.cloudflare.com`。不要重新迁移 nameserver。
2. 当前 Vercel DNS 记录由用户确认：`A @ → 216.198.79.1` 和 `CNAME www → 1b5953d58358d9c5.vercel-dns-017.com`，均为 DNS-only。根域已由 Vercel 308 跳转到 `www.91idc.me`。不要修改这些记录。
3. 在 Cloudflare Zero Trust 创建 Tunnel，为 Public Hostname 配置 `api.91idc.me` → `http://127.0.0.1:18000`。Cloudflare 会创建对应 DNS 路由。把 token 直接录入服务器 `/etc/autograder/cloudflared.token`，初始权限设为 `root:root 0600`；宿主准备好 `cloudflared` 账号后会收紧为 `root:cloudflared 0640`。不要把 token 放进 GitHub。

当前 `autograder-prd` Tunnel 已配置该 Published application route；2026-10-19 权威 DNS 查询确认 `api.91idc.me` CNAME 指向 `a4ba1fda-f0d6-4eb5-9d7a-0dac1d86e85d.cfargotunnel.com`。初始安装后 `cloudflared.service` 曾为 active/enabled、QUIC 已注册；首次部署失败回滚后，2026-10-19 最新只读检查显示 connector inactive。Token 文件仍为 `root:cloudflared 0640`，成功部署会重新启用 Tunnel。TCP/7844 的一个区域连通性检查失败，connector 原先继续使用 QUIC。

服务器不需要开放 80/443、18000、PostgreSQL 或 NATS 公网端口。SSH 只用于 GitHub Actions 部署和运维。

首次配置 Tunnel token 时，在已连接的 root SSH shell 输入，不要把 token 放进命令历史或聊天：

```bash
install -d -o root -g root -m 0700 /etc/autograder
read -r -s -p 'Cloudflare Tunnel token: ' TUNNEL_TOKEN
printf '\n'
printf '%s' "$TUNNEL_TOKEN" > /etc/autograder/cloudflared.token
unset TUNNEL_TOKEN
chmod 0600 /etc/autograder/cloudflared.token
```

### Mihomo 出站代理（仅 AutoGrader）

2026-10-19 用户选择只配置 AutoGrader 本机代理，不改 Docker daemon。提供的 Mihomo YAML 已能解析，包含 AnyTLS 节点凭据；不要提交到 Git、Doppler 或聊天。配置中的 `allow-lan: false`、`external-controller: 127.0.0.1:9090` 保持本机监听。

官方 Linux amd64-compatible 二进制固定为 v1.19.32：
`https://github.com/MetaCubeX/mihomo/releases/download/v1.19.32/mihomo-linux-amd64-compatible-v1.19.32.gz`
SHA-256：`ba3ce607747a07f948fc35780e108a4a7c7f552a38b9bd4d115f313ebcb89c20`。

使用 Xftp 将二进制压缩包、用户提供的配置（保存名 `autograder-mihomo.yaml`）、[`install-mihomo-autograder.sh`](../../scripts/install-mihomo-autograder.sh) 和 [`mihomo-autograder.service`](../../deploy/mihomo-autograder.service) 上传到服务器 `/root/`。在服务器 root shell 执行：

```bash
bash /root/install-mihomo-autograder.sh \
  /root/mihomo-linux-amd64-compatible-v1.19.32.gz \
  /root/autograder-mihomo.yaml \
  /root/mihomo-autograder.service
```

安装器核对官方包摘要、验证 Mihomo 配置、检查本机端口后，创建独立的 `mihomo-autograder.service`。配置保存在 `/etc/mihomo-autograder/config.yaml`，权限为仅 root 和该服务用户可读；安装器为 loopback 控制 API 加入随机密码。代理 mixed-port 为 `127.0.0.1:7893`。后端部署脚本和 AutoGrader API 服务只在自身进程环境使用该代理，不修改 `/etc/profile` 或 Docker daemon。

Docker 镜像拉取由 dockerd 发起，本机 Mihomo 环境变量不会代理这些拉取。当前服务器已通过 Docker daemon 直连拉取 `postgres:17-alpine` 与 `nats:2.10-alpine`，无需配置 Docker 全局代理。

### Vercel

- 项目 `auto-grader` 已绑定公开仓库 `starprince1234/AutoGrader`，`www.91idc.me` 为 Production 前端入口，根域跳转到 `www`。
- Vercel 项目的 Root Directory、Include source files outside of Root Directory、Production 环境变量和 Git 自动部署状态尚未通过项目设置页核实。确认 Root Directory 为 `B1`，并允许构建读取 `../MAIN/contracts`。仓库的 `B1/.env.production` 只含公开 API origin。Doppler `VITE_API_BASE_URL` 已核实为 `https://api.91idc.me` 并设为 `unmasked`；用户截图曾显示 Vercel Config Sync 因该变量被标为 Secret 而 Disabled，改动后仍需确认同步已恢复。
- `B1/vercel.json` 指定 Vite、`pnpm api:check && pnpm build`、`dist`、`/app/` 路由及 ignore command。ignore command 只在前端构建输入或 `MAIN/contracts/**` 改动时构建；文档修改会跳过。

### Doppler 与 GitHub Actions

项目配置为 Doppler `autograder/prd`。数据库与服务密钥位于宿主机受限文件 `/etc/autograder/runtime.env`、`/etc/autograder/setup.env`；日常 release 不重新渲染或上传这些文件。生产密码项为独立的随机 32-byte hex，不复用仓库本地集成配置。

只读且仅限 `autograder/prd` 的 Doppler service token `github-actions-autograder-prd` 已保存为 GitHub Actions repository secret `DOPPLER_TOKEN`。工作流只读取 SSH 连接字段，不注入数据库或应用密钥。`DEPLOY_SSH_HOSTKEY` 是 SHA256 fingerprint；工作流在任何 SSH 连接前固定并校验它。仓库公开，因此 server-side Git fetch 不使用额外 GitHub 凭据。

## 2. 服务器发布

`main` 的后端运行代码、契约、测试与部署文件变化触发 Actions：安装锁定的集成后端依赖 → 后端 lint、host/readiness 回归测试与 shell 语法检查 → SSH 发布同一 SHA。前端由 Vercel 验证和发布，不在后端流水线重复构建。全模块数据库、沙箱与文档验收按专项需要执行，不作为每次发布的前置条件。

每次发布更新服务器上的部署控制器与预检脚本，避免仓库已经修复而服务器继续执行旧代码。服务器通过本机代理获取目标 SHA，按 release manifest 打包运行代码并用 `uv sync --locked --extra modules --no-dev` 创建独立环境。构建成功后停止 API、应用当前数据库结构、更新 systemd units、切换 current 并启动 API 与 Tunnel。

预检只确认实际运行依赖、cgroup v2、受保护的配置文件以及 PostgreSQL/NATS 可用。首次部署残留、已有缓存/JWKS、可用内存的任意阈值、无关网站的连通性都不再决定能否上线。宿主运行时与服务账号仍需一次性准备，PostgreSQL/NATS 不随应用发布重建。

本机与公网 `/health/ready` 均须报告 ready 且包含健康的 B2/B3/B4/B5；模块标识读取 `name`。失败时保留候选目录、current 与服务现场供诊断，不自动回退指针、删除候选、禁用服务或恢复数据库。公网不可达不会主动关闭已经正常运行的本机 API。失败仍会使 Actions 明确失败。

手动操作只需要选择当前 main 和 operation，不再填写历史 run ID 或旧 SHA：

```bash
gh workflow run deploy-backend.yml --ref main -f operation=deploy
gh workflow run deploy-backend.yml --ref main -f operation=diagnose
gh workflow run deploy-backend.yml --ref main -f operation=preflight
```

首次生成服务器环境文件仍用 `operation=bootstrap-host`；已有环境文件保持原值。诊断不以预检通过为前提。密钥、SSH 主机指纹、最小权限数据库账号与 nsjail 隔离保持有效。

### 邮件通道变量（`operation=sync-mail-channel`）

生产环境文件只渲染一次（`bootstrap-host`），日常发布不会重写它。自助注册需要的 `SMTP_*` 因此单独有一套
显式操作：

```bash
gh workflow run deploy-backend.yml --ref main -f operation=sync-mail-channel
```

它只做四件事：

1. 从 Doppler `autograder/prd` 取 `SMTP_HOST/PORT/USER/PASS/FROM/FROM_NAME/TLS_MODE/TIMEOUT_SECONDS`；
2. 把渲染结果与 `MAIN/deploy/merge-env-keys.py` 上传到服务器 `/tmp` 的 0700 暂存目录；
3. **只合并这八个键**进 `/etc/autograder/runtime.env`：缺失则追加、值变则原地替换，其余字节不动；
   原子写入（同目录临时文件 + `os.replace`），保持 `0600 root:root`；
4. 重启 `autograder.service`，并在 30 次轮询内等待 `/health/ready` 出现 `"ready": true`。

这个操作不会重新渲染 `runtime.env` 的其余部分，也不会触发发布。

**缺口是怎么来的**：这些变量曾经确实存在于 Doppler，但 `write-runtime-env.mjs` 的 `runtimeEntries`
是硬编码白名单，没列进去 ⇒ 文件里没有 ⇒ 进程读不到 ⇒ 启动日志
`module.registration_mail_unconfigured` 且注册端点返回 `503`。现在两条渲染路径共用
`MAIN/deploy/mail-env.mjs`，不会各维护一份。

**手工等价操作**（不想再跑一次 workflow 时）：把同样八行追加到 `/etc/autograder/runtime.env`
（`root:root 0600`）后 `systemctl restart autograder.service`；手工方式需自行保证引号与转义正确，
写入前确认文件不是符号链接。

### 模型 provider 变量（`operation=sync-llm-config`）

同一条"只渲染一次"的约束也适用于模型 provider：`LLM_BASE_URL` / `LLM_MODEL_NAME` / `LLM_APIKEY`
只写进首次 `bootstrap-host` 渲染的 `runtime.env`，日常发布不会重写它。已经存在的宿主因此需要一次显式同步：

```bash
gh workflow run deploy-backend.yml --ref main -f operation=sync-llm-config
```

它从 Doppler `autograder/prd` 取这三个键，用同一个 `MAIN/deploy/merge-env-keys.py` **只合并这三个键**
进 `/etc/autograder/runtime.env`（原子写入，0600 root:root，日志只打印键名不打印值），然后重启
`autograder.service` 并在 30 次轮询内等待 `/health/ready` 就绪。

`LLM_BASE_URL` 是 **base**：B3 自己追加 `/v1/responses`（Responses API）。把完整端点写进 base 变量会
得到一个"存在但永远失败"的 provider。三个键必须同时存在——只配一部分会让页面先显示"可用"、再在第一次
生成时失败，那比缺少配置更难解释。缺任意一项时 B3 如实上报 `provider_status=unavailable`，页面显示
"AI 出题暂未开通"，手动出题不受影响。

**手工等价操作**：把三行追加到 `/etc/autograder/runtime.env` 后重启服务（同样需自行保证引号与转义）。


## 3. 空库重建与后续数据

2026-10-20 用户已授权删除当前生产业务数据，并要求后续发布保留新数据。重建由独立的 [`reset-database.sh`](../../deploy/reset-database.sh) 执行，不接入 push 或日常 deploy，也不提供自动清库开关。

在新 release 环境已构建好、服务账号及基础设施已准备后，由 root 明确指定 release：

```bash
bash /opt/autograder/releases/<release>/MAIN/deploy/reset-database.sh /opt/autograder/releases/<release>
```

该命令停止 API，删除并重建 b2/b3/b4 schemas（包括用户、课程、题库、提交、评测和版本表），重新供给角色权限并从空库建立当前表结构。它不删除 PostgreSQL 实例、其他数据库、JWT 密钥或应用文件；完成后自动启动 API 并等待本地 readiness。随后仍需单独创建首个管理员。历史 NATS 消息与磁盘上的 artifact 不由此数据库脚本清理。

Alembic 继续承担创建及更新表结构的职责；空库也需要这一过程。正常发布不会清空业务数据，不再为每次 release 创建迁移前 dump、运行恢复演练或要求备份文件名与失败版本匹配。本流程未建立自动备份策略，后续有真实数据时备份应作为独立运维任务安排。

## 4. 运维与排障

```bash
systemctl status autograder cloudflared
journalctl -u autograder -n 100 --no-pager
journalctl -u cloudflared -n 100 --no-pager
docker compose --project-name autograder --env-file /etc/autograder/runtime.env \
  --file /opt/autograder/current/MAIN/compose.production.yaml ps
curl --fail http://127.0.0.1:18000/health/ready
```

历史备份可能仍保存在服务器，当前发布流程不管理或删除它们。首个管理员由 B4 运维命令单独创建；部署不会写入默认账号或默认密码。root SSH shell 中可用隐藏输入把密码只传给一次性命令：

```bash
set -a
source /etc/autograder/runtime.env
set +a
read -r -s -p 'Initial admin password: ' B4_BOOTSTRAP_ADMIN_PASSWORD
printf '\n'
export B4_BOOTSTRAP_ADMIN_PASSWORD
runuser --preserve-environment --user autograder -- \
  /opt/autograder/current/MAIN/.venv/bin/python -m autograder_b4.ops.bootstrap_admin \
  --username admin
unset B4_BOOTSTRAP_ADMIN_PASSWORD
```

`/health/ready` 证明模块、数据库与消息依赖已启动，不等于 nsjail 安全门禁、Linux 资源实测或完整业务流程已验收；这些证据仍由 [`linux-acceptance.md`](linux-acceptance.md) 和阶段记录维护。
