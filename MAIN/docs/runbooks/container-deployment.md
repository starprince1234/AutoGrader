# 运行手册：容器部署

本手册描述**唯一对外的交付路径**：在一台只有 Docker 的机器上，从空库把 AutoGrader 平台起起来。它不需要本机 Python、不需要本机 Node、不需要任何未跟踪的 env 文件。

配套：`local-development.md`（Windows 开发）、`integration.md`（组装模型与即插即用验证）、`troubleshooting.md`（故障分诊）。

---

## 1. 一条命令

```bash
cd MAIN
docker compose up -d --build
docker compose ps            # host 应为 healthy
curl -s http://localhost:18000/health/ready
```

**成功标志**：`/health/ready` 返回 `{"ready": true, ...}`，且 `modules` 中同时出现 **B2、B3、B4**，三者 `healthy` 均为 `true`。

第一次启动比后续慢：镜像要构建 B1（`npm install` + `vite build`），容器要生成签名密钥、跑三个模块的迁移并做 B4 的角色供给。`host` 的 healthcheck `start_period` 为 60 s，是为这段一次性工作留的。

停止：

```bash
docker compose down          # 保留卷（数据、签名密钥、工件都在卷里）
docker compose down -v       # 连卷一起删；下一次 up 就是真正的首次启动
```

---

## 2. 启动顺序（为什么不是"起 uvicorn"）

`MAIN/host/entrypoint.sh` 是容器里唯一的第一启动序列，顺序是有依赖的，不能重排：

| # | 步骤 | 为什么必须在这一步 |
|---|---|---|
| 1 | 等 PostgreSQL 可连接（默认 120 s，`POSTGRES_WAIT_SECONDS` 可调） | `depends_on: service_healthy` 只覆盖 compose 场景；`docker run` 与"库很慢"也必须给出有界的、说明原因的等待，而不是崩溃重启 |
| 2 | 生成 Ed25519 签名密钥（已存在则跳过），并由它导出 JWKS | B4 在装载路由时就要有密钥（缺密钥是**硬启动失败**，这是设计）；B2 从**文件**读公钥集，而它在宿主进程还没开始监听时就被装配，不可能去请求自己的端点 |
| 3 | `alembic upgrade head`（b2 → b4 → b3） | `init.sql` 只建 schema、角色与 schema 级 `USAGE`；**表级授权来自各模块的迁移**，B4 的表授权来自第 4 步。空库不迁移 = 没有表 |
| 4 | `python -m autograder_b4.ops.provision` | B4 自己拥有 `b4` 的表授权；这是 B4 唯一允许开特权连接的运维入口，幂等，并在结束后复验角色最小权限 |
| 5 | `exec uvicorn ...` | 交给宿主进程；用 `exec` 是为了让 uvicorn 直接持有容器主进程并正确收到终止信号 |

b2 与 b4 的迁移都用**平台超级用户** DSN 运行，并且必须在同一身份下运行：`init.sql` 在**那个身份**上声明了 `ALTER DEFAULT PRIVILEGES`，而 `init.sql` 执行时表还不存在。超级用户 DSN（`PLATFORM_DATABASE_URL`）只存在于第 3、4 步；宿主的模块设置视图由 `<MODULE>_` 前缀变量构造（ADR-0008），因此 B2/B3/B4 各自以自己的最小权限角色连接，**应用从不以 `autograder_platform` 连接**。

---

## 3. 环境变量（`compose.yaml` 是权威）

| 变量 | 用途 | 谁使用 |
|---|---|---|
| `PLATFORM_DATABASE_URL` | 平台超级用户 DSN | **仅** entrypoint 的迁移与 `ops.provision` / `ops.bootstrap_admin` |
| `B4_ADMIN_DATABASE_URL` | `ops.provision` 的特权 DSN | B4 运维命令 |
| `B4_DB_PASSWORD` | `autograder_b4` 角色的口令 | `ops.provision` |
| `JWT_PRIVATE_KEY_FILE` | Ed25519 私钥路径（卷内） | B4 |
| `B4_MODULE_ROOT` | 持有 `MAIN/contracts` 与四个 `B*/` 根目录的目录（镜像内 `/app`） | B4 的注册表与迁移 head 探针 |
| `B1_DIST_DIR` | 构建后的 SPA 目录（镜像内 `/app/B1/dist`） | host 静态挂载 |
| `B2_DATABASE_URL` / `B2_ARTIFACT_ROOT` / `B2_WORKSPACE_ROOT` / `B2_JWKS_PATH` / `B2_NATS_URL` / `B2_B3_BASE_URL` / `B2_B3_AUTHORIZATION` | B2 的库、内容寻址存储、沙箱工作区、公钥集、消息与取包端点 | B2 |
| `B3_DATABASE_URL` / `B3_DATABASE_PROTECTED_URL` / `B3_SERVICE_TOKEN` / `B3_NATS_URL` | B3 的普通角色与受保护角色、服务令牌 | B3 |
| `B4_JWKS_URL` | B4 公钥集的地址（B3 消费） | B3 |
| `B4_DATABASE_URL` / `B4_NATS_URL` / `B4_JWT_ISSUER` / `B4_JWT_AUDIENCE` | B4 的身份、消息与令牌声明 | B4 |

两个命名细节值得单独记住，它们各自都以"配置了但没人读"的形式制造过故障：

- B3 读的是 **`B4_JWKS_URL`**（公钥集的**提供方**命名），不是 `B3_JWKS_URL`。写 `B3_JWKS_URL` 时，宿主会把它在 B3 的设置视图里重暴露为 `JWKS_URL`——**没有任何代码读这个名字**，于是令牌验证器根本不建：`/health/ready` 报告 `token_verifier=None`，而受认证路由返回 500 而不是 401。这是实测出来的（`phases/active/phase-01-integration-runtime.md` §4.7）。前缀机制按**消费者**命名前缀，但变量名本身可以按**提供方**命名，两者不是一回事。
- `B2_B3_AUTHORIZATION` 的值是**整个 `Authorization` 头**，含 scheme（`Bearer …`）。B2 原样发送（`integration/adapters.py`）。

**宿主端口可参数化**：`POSTGRES_PORT` / `NATS_PORT` / `HOST_PORT`（默认 55432 / 54222 / 18000）。只有宿主侧端口变化，容器内地址与所有 DSN 不变，所以本手册的其余步骤无需跟着改。

---

## 4. 与既有实例并存

同一台机器上再起一套（例如验证首次启动，而不打扰正在服务的实例）：

```bash
cd MAIN
POSTGRES_PORT=65432 NATS_PORT=64222 HOST_PORT=28000 \
  docker compose -p agverify up -d --build
```

单独的 compose project 名（`-p`）让容器、网络与卷全部与既有实例隔离。验证完：

```bash
docker compose -p agverify down -v
```

**不要**用 `docker compose down` 去清理由别的 project 名启动的栈，也不要在未确认的情况下删除不属于本系统的历史卷。

---

## 5. 首次启动后的可选步骤：首个管理员

空库里没有任何管理员，而 `POST /api/v1/users` 需要管理员——这个先有鸡还是先有蛋的问题由 B4 的运维命令解决（幂等，口令不回显、不落日志）：

```bash
docker compose exec host python -m autograder_b4.ops.bootstrap_admin \
    --username admin --password '<a-real-password>'
```

它使用最小权限的 `autograder_b4` 角色，因此不需要（也不会获得）提升的库权限。

有了管理员之后，可以用仓库里的播种门走公开 HTTP API 造出可演示数据（教师、课程、课序、中文表头 XLSX 名单、题目、作业）：

```bash
python MAIN/scripts/seed_integration.py --base-url http://127.0.0.1:18000
```

---

## 6. 故障定位

| 现象 | 原因 | 处置 |
|---|---|---|
| `host` 一直 `starting`，日志停在 `waiting for PostgreSQL` | PostgreSQL 未就绪或 DSN 不对 | `docker compose ps postgres`；`docker compose logs postgres`。等待上限由 `POSTGRES_WAIT_SECONDS` 控制 |
| `ERROR: … is required and must not be empty` | 某个必需变量缺失 | entrypoint 会**点名**变量；在 `compose.yaml` 的 `host.environment` 中补上。没有默认值可用，这是有意的 |
| `B4_MODULE_ROOT=… does not contain MAIN/contracts` | 模块根指错 | 容器内应为 `/app`（镜像根即模块根） |
| `/health/ready` 中 `B4` 为 `healthy: false`，详情含 `postgres unavailable` | B4 的库角色连不上 | `docker compose exec host python -m autograder_b4.ops.provision --verify-only` |
| `/health/ready` 中 `B4` 详情含 `migration head drift` | 迁移没跑或没跑完 | `docker compose logs host \| grep migrating`；确认三个模块都到 head |
| `/health/ready` 中 `B4` 详情含 `refusing to start: database role … is over-privileged` | 应用被给了超级用户 DSN | **不要**放宽这个守卫。检查模块是否读到了 `B4_DATABASE_URL`（前缀变量），而不是全局 `DATABASE_URL` |
| `/health/ready` 中 `B2` 为 `healthy: false`，详情含 `B2_ARTIFACT_ROOT is required` 或 `no JWKS configured` | B2 的必需项没配 | 见 §3 的 B2 行；`B2_JWKS_PATH` 指向卷内 `jwks.json`，由 entrypoint 生成 |
| `/health/ready` 中 `B3` 为 `healthy: false`，详情含 `database unavailable` | B3 的库角色连不上 | 两个 DSN 都要给：普通角色与受保护角色 |
| `/app` 返回 404 但 `/health/live` 正常 | `B1_DIST_DIR` 指向的目录没有 `index.html` | 镜像内应为 `/app/B1/dist`；若是自建镜像，确认 B1 阶段构建成功（`docker compose build host` 的输出） |
| `docker compose up` 报端口被占用 | 宿主端口已被别的服务占用 | 用 §4 的 `POSTGRES_PORT` / `NATS_PORT` / `HOST_PORT` 改宿主侧端口 |

---

## 7. 本手册的验证记录

本手册的每一条启动路径都在真实环境执行过：空卷首次启动、三插件 readiness、`/app` SPA（含浏览器实测）、
容器内签发令牌并读写真实数据、`docker compose ps` 的 `healthy` 状态。逐条命令与读数记在
`../phases/active/phase-01-integration-runtime.md` §13，不在本手册内重复。

**未覆盖**：

- Linux 安全与资源门禁（必需 cgroup v2 + nsjail，见 `linux-acceptance.md`）。容器路径证明的是**应用可以交付并自举**，不是沙箱已验收。
- 三个模块的**完整**业务链（提交 → 评测 → 成绩）。本手册覆盖到"起得来、签得出令牌、读写得到真实数据"；评测链需要 Linux 沙箱，在 Windows 上按 ADR-0004 如实返回 `infrastructure_error`。
- `MAIN/scripts/seed_integration.py` 的发布一步（对 B3 当前的参考解答存证门禁返回 422，是该脚本与 B3 之间的既有不一致，与容器路径无关）。
