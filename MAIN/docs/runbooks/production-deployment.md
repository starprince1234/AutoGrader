# 运行手册：生产部署

生产拓扑：B1 在 Vercel 的 `www.91idc.me/app/`；API 在 Linux 主机上以非 root systemd 服务运行，只监听 `127.0.0.1:18000`；Cloudflare Tunnel 将 `api.91idc.me` 转发到 API。PostgreSQL 与 NATS 只绑定 loopback。

前端 DNS 与 Vercel 自定义域名已按用户交接记录完成。2026-10-19 通过 SSH 实测服务器为 OpenCloudOS 9.6 x86_64、Docker Compose 5.4.0、cgroup v2。首次生产预检当时通过：4 CPU、3655 MiB RAM（当时可用 1602 MiB）、`/var/lib` 所在磁盘总量 39 GiB/可用 15 GiB，首部署项目、卷、服务及目标端口无冲突。第一次部署随后中断，服务器目前处于本手册 §2 所述的受限续跑状态。

## 1. 首次准备

### 域名与 DNS

1. Cloudflare zone `91idc.me` 已为 Active，阿里云 nameserver 已指向 Cloudflare：`algin.ns.cloudflare.com`、`priscilla.ns.cloudflare.com`。不要重新迁移 nameserver。
2. 当前 Vercel DNS 记录由用户确认：`A @ → 216.198.79.1` 和 `CNAME www → 1b5953d58358d9c5.vercel-dns-017.com`，均为 DNS-only。根域已由 Vercel 308 跳转到 `www.91idc.me`。不要修改这些记录。
3. 在 Cloudflare Zero Trust 创建 Tunnel，为 Public Hostname 配置 `api.91idc.me` → `http://127.0.0.1:18000`。Cloudflare 会创建对应 DNS 路由。把 token 直接录入服务器 `/etc/autograder/cloudflared.token`，初始权限设为 `root:root 0600`；首次部署创建 `cloudflared` 账号后会收紧为 `root:cloudflared 0640`。不要把 token 放进 GitHub。

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

- 项目 `auto-grader` 已绑定私有仓库 `starprince1234/AutoGrader`，`www.91idc.me` 为 Production 前端入口，根域跳转到 `www`。
- Vercel 项目的 Root Directory、Include source files outside of Root Directory、Production 环境变量和 Git 自动部署状态尚未通过项目设置页核实。确认 Root Directory 为 `B1`，并允许构建读取 `../MAIN/contracts`。仓库的 `B1/.env.production` 只含公开 API origin。Doppler `VITE_API_BASE_URL` 已核实为 `https://api.91idc.me` 并设为 `unmasked`；用户截图曾显示 Vercel Config Sync 因该变量被标为 Secret 而 Disabled，改动后仍需确认同步已恢复。
- `B1/vercel.json` 指定 Vite、`pnpm api:check && pnpm build`、`dist`、`/app/` 路由及 ignore command。ignore command 只在前端构建输入或 `MAIN/contracts/**` 改动时构建；文档修改会跳过。

### Doppler 与 GitHub Actions

项目配置为 Doppler `autograder/prd`。部署渲染脚本只接受 64 位 hex 的 PostgreSQL 与 B2↔B3 服务密钥，避免 DSN/SQL 字符转义问题。当前配置中的数据库 DSN 使用 `127.0.0.1:55432`（仓库本地集成 PostgreSQL 端口），其密码与仓库本地集成默认值相同；B2↔B3 token 也与本地集成默认值相同。为避免覆盖现有字段，已在 Doppler 新增独立的 `POSTGRES_PASSWORD`、`B2_DB_PASSWORD`、`B3_APP_DB_PASSWORD`、`B3_INTERNAL_DB_PASSWORD`、`B4_DB_PASSWORD` 和 `B2_B3_SERVICE_TOKEN`，每个值均为随机 32-byte hex。

只读且仅限 `autograder/prd` 的 Doppler service token `github-actions-autograder-prd` 已保存为 GitHub Actions repository secret `DOPPLER_TOKEN`。工作流通过 Doppler action 的 step outputs 按步骤传递必需字段，不把全部 Doppler secrets 注入整个 job，也不把值写入仓库。`DEPLOY_SSH_HOSTKEY` 已通过 Doppler CLI 写入，去除了命令输出中的主机名后缀，并验证为 SHA256 fingerprint 格式；值未输出。工作流会在任何 SSH 连接前拒绝缺失或格式不正确的 fingerprint。

## 2. 服务器首次部署

`DEPLOY_SSH_USER` 当前为 root。2026-10-19 首次只读预检已通过，确认 DNF、systemd、cgroup v2、Compose v2、资源余量、端口空闲且没有同名 AutoGrader 服务、卷或 Compose 项目。GitHub Actions 通过 `.github/workflows/deploy-backend.yml` 执行发布前验证与部署：

1. 推送后端源码、迁移、模块清单、契约、生产 Compose 或测试相关变更时，GitHub Actions 运行 B1 验证、B1–B5 测试和 MAIN 契约/文档门禁；通过后才读取部署字段。SSH 预检脚本放入本次运行独有的 `/tmp/autograder-preflight-<run-id>-<attempt>` 目录，避免与其他项目的临时文件冲突。连接后先执行只读预检，要求 DNF、systemd、cgroup v2、Docker Compose v2、足够资源、无端口或同名项目冲突。首次部署不支持的环境会在改动服务前停止。
2. 通过固定版本的 `uv` 安装锁定的 Python 依赖；首次部署从固定的 nsjail 3.4 源码构建并运行 `MAIN/deploy/patch-nsjail-protobuf4-compat.py`，兼容 OpenCloudOS 9 的 Protobuf 24.x（protobuf 4 API）。构建后执行 `nsjail --help` 启动检查，再安装 Cloudflare `cloudflared`。专用 Python runtime 由 `autograder` 所有；锁定 release 权限时不跟随 `.venv/bin/python` 符号链接修改 runtime 文件。
3. 先将包含凭据的临时上传目录设为 root-only，再把发布代码放入 `/opt/autograder/releases/<sha>-<run>`，安装只读 runtime env 到 `/etc/autograder/runtime.env`。
4. 以 Compose 项目 `autograder` 启动 PostgreSQL/NATS；卷名固定，后端发布不会删除业务卷。生产端口为 loopback `25432`（PostgreSQL）和 `25222`（NATS），与当前 Doppler 中的本地端口不同。
5. root-only `setup.env` 仅供 Compose 初始化与一次性迁移/角色供给；API 的常驻 systemd 服务只读取 `runtime.env`，不继承平台 superuser DSN。systemd 以 `autograder` 用户在 cgroup v2 委派子树运行 API；`cloudflared` 用户运行 Tunnel connector。
6. 每次迁移前在 `/var/lib/autograder/backups` 保存权限为 `0600` 的 PostgreSQL custom dump。迁移或 readiness 失败时，部署脚本停止新服务、恢复数据库快照、切回上一版代码并恢复原本运行的 API/Tunnel。API 会在迁移期间短暂停止；成功后保留近五份数据库备份和近三版代码。
7. 发布只有在本机 `/health/ready` 与经 Cloudflare 的 `https://api.91idc.me/health/ready` 都返回 `ready: true` 后才报告成功。首次部署失败时没有旧版本可切回，服务会保持停止且数据保留，需先查明失败原因再重试。

首次部署若在准备数据库迁移时中断，重试会识别这一组受限的部分状态：无 current release、API unit inactive、一个健康 PostgreSQL 与一个运行中的 NATS 容器、匹配的两个 AutoGrader Compose volumes，以及与上次 Compose 配置路径匹配的 root-only pre-migration dump。数据库/NATS 容器、volumes 和备份会被保留并复用；额外容器、卷或备份、配置不匹配、活动 API 进程时预检停止。`/var/lib/autograder` 的预建数据子目录必须为空。专用 Python runtime 可以为空，或只含一个结构完整的 uv-managed CPython 3.12 安装；预检核对版本别名、文件类型、属主和私有目录权限，部署前会把它的所有权归一到 `autograder`。uv cache 必须保持 AutoGrader 专用属主与目录权限；不清理已有缓存。

纯运维脚本或本工作流变更不再自动触发五模块全量测试。此类变更在本地完成 `bash -n` 后，可手动执行只读预检或部署；必须引用一个 verify job 成功的工作流 run ID 和它验证的 commit SHA。手动入口会确认该 SHA 是当前部署的祖先、verify job 确实成功，且两者之间只有部署运维文件变化。示例：

```bash
VERIFIED_RUN_ID="<completed-run-id>"
VERIFIED_COMMIT_SHA="<verified-40-character-sha>"

gh workflow run deploy-backend.yml --ref main \
  -f operation=preflight \
  -f "verified_run_id=$VERIFIED_RUN_ID" \
  -f "verified_commit_sha=$VERIFIED_COMMIT_SHA"

gh workflow run deploy-backend.yml --ref main \
  -f operation=deploy \
  -f "verified_run_id=$VERIFIED_RUN_ID" \
  -f "verified_commit_sha=$VERIFIED_COMMIT_SHA"
```

应用源码、迁移或契约发生变化时不能复用旧 verify 结果，需由 push 工作流重新通过全部门禁。

发布归档通过 Git attributes 排除 `MAIN/references/**` 课程课件；它们不参与服务器运行，避免每次发布都重复上传。

首次数据库初始化会由 `initialize-db.sh` 把各模块专属密码传给 PostgreSQL；B2/B3/B4 使用各自最小权限账号。Ed25519 JWT 私钥及应用数据持久化在 `/var/lib/autograder`。

## 3. 自动更新范围

- B1 的构建输入或 `MAIN/contracts/**` 改动由 Vercel Git Integration 自动部署 `www.91idc.me`；文档-only 修改跳过。
- 后端源码、迁移、模块清单、契约、production Compose 或部署脚本改动，在推送到 `main` 后触发 GitHub Actions 更新服务器。
- `concurrency` 按 production group 排队，不会并发切换当前 release。

## 4. 运维与排障

```bash
systemctl status autograder cloudflared
journalctl -u autograder -n 100 --no-pager
journalctl -u cloudflared -n 100 --no-pager
docker compose --project-name autograder --env-file /etc/autograder/runtime.env \
  --file /opt/autograder/current/MAIN/compose.production.yaml ps
curl --fail http://127.0.0.1:18000/health/ready
```

备份目前保存在同一台服务器，不能防止服务器磁盘或整机故障；远端副本尚未配置。首个管理员由 B4 运维命令单独创建；部署不会写入默认账号或默认密码。root SSH shell 中可用隐藏输入把密码只传给一次性命令：

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
