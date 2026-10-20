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

- 项目 `auto-grader` 已绑定公开仓库 `starprince1234/AutoGrader`，`www.91idc.me` 为 Production 前端入口，根域跳转到 `www`。
- Vercel 项目的 Root Directory、Include source files outside of Root Directory、Production 环境变量和 Git 自动部署状态尚未通过项目设置页核实。确认 Root Directory 为 `B1`，并允许构建读取 `../MAIN/contracts`。仓库的 `B1/.env.production` 只含公开 API origin。Doppler `VITE_API_BASE_URL` 已核实为 `https://api.91idc.me` 并设为 `unmasked`；用户截图曾显示 Vercel Config Sync 因该变量被标为 Secret 而 Disabled，改动后仍需确认同步已恢复。
- `B1/vercel.json` 指定 Vite、`pnpm api:check && pnpm build`、`dist`、`/app/` 路由及 ignore command。ignore command 只在前端构建输入或 `MAIN/contracts/**` 改动时构建；文档修改会跳过。

### Doppler 与 GitHub Actions

项目配置为 Doppler `autograder/prd`。数据库与服务密钥位于宿主机受限文件 `/etc/autograder/runtime.env`、`/etc/autograder/setup.env`；日常 release 不重新渲染或上传这些文件。生产密码项为独立的随机 32-byte hex，不复用仓库本地集成配置。

只读且仅限 `autograder/prd` 的 Doppler service token `github-actions-autograder-prd` 已保存为 GitHub Actions repository secret `DOPPLER_TOKEN`。工作流只读取 SSH 连接字段，不注入数据库或应用密钥。`DEPLOY_SSH_HOSTKEY` 是 SHA256 fingerprint；工作流在任何 SSH 连接前固定并校验它。仓库公开，因此 server-side Git fetch 不使用额外 GitHub 凭据。

## 2. 服务器发布

GitHub Actions 先对本次 `main` SHA 执行验证，再通过已固定主机指纹的 SSH 发送完整 SHA 和运行尝试号。仓库是公开的，服务器不接收 GitHub token。服务器上的 `/usr/local/sbin/autograder-deployctl` 核验 GitHub `main` 当前指向该 SHA 后，经实测有效的本机代理 `127.0.0.1:7893` 增量获取 Git 对象，在 `/opt/autograder/git-cache` 复用对象缓存，并按目标 SHA 的 [`release-manifest.txt`](../../deploy/release-manifest.txt) 在服务器本地生成 release。Git 代理设置只作用于 fetch/archive。

首次安装只进行一次 `bootstrap-host` 操作：若两份 root-only 环境文件都不存在，工作流从 Doppler 生成并经固定主机指纹的 SSH 安装；若两份都已正确存在则保留原值，只有部分存在或属主/权限不符时停止。该操作同时安装部署控制器与只读预检脚本，并将已识别的 uv-managed Python 可执行文件和 Cloudflare token 权限收敛到服务所需的精确属主/模式。之后日常发布请求只含 SHA 和 attempt，不上传源码归档、运行环境文件或 GitHub 凭据。PostgreSQL/NATS 容器及数据卷由独立基础设施流程管理。

网络对照测量（2026-10-19，生产服务器 x86_64）：经代理的 GitHub `ls-remote` 约 0.64–0.67 秒，blobless baseline fetch 约 1.13 秒，增量 fetch 约 1.01 秒；runtime archive 3,698,574 bytes 在 4.819 秒完成。直连 `ls-remote` 约 1.2–1.6 秒，但对同一 SHA 的 runtime archive 在 180 秒后超时。首次测量使用了 Actions 短时只读 token；公开仓库的发布流程不需要此凭据，后续会用无认证路径重测。

每次 release 使用锁定的 `uv.lock` 在服务器构建独立 `.venv`，明确设置 `UV_PROJECT_ENVIRONMENT` 到候选版本目录，并在迁移前验证 Python 与 Alembic 可执行文件。`uv`、Python runtime、nsjail、cloudflared、systemd units 和 PostgreSQL/NATS 属宿主或基础设施生命周期；应用发布不会安装这些依赖、执行 `docker compose up`、重建容器或改动数据卷。基础设施未就绪时预检失败并停止。

数据库迁移前在 `/var/lib/autograder/backups` 保存 root-only PostgreSQL custom dump。候选构建失败时不会触碰数据库；迁移准备开始后若失败，发布脚本保留备份、回退 release 指针并保持 API/Tunnel 停止，不自动执行数据库回滚，必须按 revision 状态前向修复。尚未开始迁移且已有旧 release 时，应用可切回旧指针并恢复先前运行状态。API 仅监听 `127.0.0.1:18000`，Cloudflare Tunnel 仍使用既有 `api.91idc.me` 路由。成功判据是本机和公网 `/health/ready` 都返回 `ready: true`。

运行手动的运维操作时，必须引用同一仓库 `main` 上成功的 verify run ID 和 SHA；入口核验两者间只有运维文件变化。首次安装控制器：

```bash
VERIFIED_RUN_ID="<completed-run-id>"
VERIFIED_COMMIT_SHA="<verified-40-character-sha>"

gh workflow run deploy-backend.yml --ref main \
  -f operation=bootstrap-host \
  -f "verified_run_id=$VERIFIED_RUN_ID" \
  -f "verified_commit_sha=$VERIFIED_COMMIT_SHA"
```

只读检查用 `operation=preflight`；部署用 `operation=deploy`，参数相同。应用、迁移或契约改动不得复用旧 verify 结果；应由 push workflow 对目标 SHA 完成完整门禁。

首次数据库初始化会由 `initialize-db.sh` 把各模块专属密码传给 PostgreSQL；B2/B3/B4 使用各自最小权限账号。Ed25519 JWT 私钥及应用数据持久化在 `/var/lib/autograder`。

## 3. 自动更新范围

- B1 的构建输入或 `MAIN/contracts/**` 改动由 Vercel Git Integration 自动部署 `www.91idc.me`；文档-only 修改跳过。
- 后端源码、迁移、模块清单、契约、锁文件或生产 Compose 改动，在推送到 `main` 后由 GitHub Actions 验证并请求服务器部署同一 SHA。
- 宿主 bootstrap、部署控制器、发布脚本与预检逻辑不随应用 push 自动执行；变更后通过手动入口复用成功的 verify run，必要时单独安装/更新控制器。
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
