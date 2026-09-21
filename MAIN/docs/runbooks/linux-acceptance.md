# 运行手册：Linux 验收

**为什么需要单独的 Linux 验收**：本系统最重要的两项保证——**沙箱安全**与**资源账本**——依赖 Linux 内核能力（cgroup v2、命名空间、seccomp-bpf），在 Windows 上**不存在**。因此在 Windows 上完成的任何验证都**不能**用于宣称这两项已通过。

验收目标环境：**Linux x64（Debian 系）**，与课程材料中 Alpha 项目明示的目标运行环境一致。

---

## 1. 环境要求

| 项 | 要求 | 检查命令 |
|---|---|---|
| 内核 | 支持 cgroup v2（≥4.15，建议 5.10+） | `uname -r` |
| cgroup 版本 | **统一层级（cgroup v2）** | `stat -fc %T /sys/fs/cgroup` → 应为 `cgroup2fs` |
| 非特权用户命名空间 | 启用 | `sysctl kernel.unprivileged_userns_clone`（若存在）应为 `1` |
| nsjail | 已安装且可执行 | `nsjail --help` |
| Docker + Compose v2 | 已安装 | `docker compose version` |
| uv / Node 24 / pnpm | 已安装 | `uv --version`、`node --version` |
| 资源 | 1 vCPU、512 MB 内存、10 GB 磁盘 | `nproc`、`free -m`、`df -h` |

### 1.1 若是 cgroup v1

需要切换到 cgroup v2（内核参数 `systemd.unified_cgroup_hierarchy=1`）并重启。

**不得**在 cgroup v1 上"凑合验收"资源限制：限额语义不同，结论不可迁移。

### 1.2 nsjail 在容器中运行

若 host 自身跑在容器里，nsjail 需要额外权限（通常需要 `--privileged` 或特定 capability 与 seccomp 放行）。

> ⚠️ **已知张力**：这与"最小权限"原则冲突。验收时**必须记录实际采用的方案与其风险**，不允许含糊处理。若无法在不放宽权限的情况下运行 nsjail，应作为**阻塞项**上报，而不是放宽权限后宣称通过。

---

## 2. 验收步骤

### 2.1 契约与文档门禁（与平台无关，但必须在此环境复跑）

```bash
cd MAIN
uv sync --all-groups --extra modules
uv run python -m autograder_host.checks
uv run pytest
```

**通过判据**：输出 `contracts, manifests and requirements: ok (...)` 与 `module documentation governance and links: ok`。

### 2.2 各模块门禁

```bash
for m in B2 B3 B4; do
  ( cd "$m" && uv sync --all-groups && uv run ruff check . && uv run pytest ) || echo "FAILED: $m"
done
```

### 2.3 迁移

```bash
for m in B2 B3 B4; do
  ( cd "$m" && uv run alembic upgrade head ) || echo "MIGRATION FAILED: $m"
done
```

**通过判据**：空库可从零迁移到 head；head 与各 `module.yaml: database.migration_head` 一致。

### 2.4 启动与 readiness

```bash
cd MAIN
docker compose up -d
sleep 10
curl -s localhost:18000/health/live
curl -s localhost:18000/health/ready
```

**通过判据**：`live` 返回 `ok`；`ready.ready == true` 且 `modules` 列出 B2/B3/B4，各自 `healthy: true`。

### 2.5 数据库权限门禁（G-DB-01 / G-DB-02 / G-DB-05）

```bash
# G-DB-02：应用不得使用超级用户
# 以超级用户作为 DATABASE_URL 启动 host → 必须失败

# G-DB-01：跨 schema 访问必须被数据库拒绝
docker compose exec postgres psql -U autograder_platform -d autograder -c \
  "SET ROLE b2_role; SELECT count(*) FROM b3.questions;"
# 期望：permission denied for schema b3

# G-DB-05：不存在跨 schema 外键
docker compose exec postgres psql -U autograder_platform -d autograder -c \
  "SELECT tc.constraint_name FROM information_schema.table_constraints tc
    WHERE tc.constraint_type='FOREIGN KEY'
      AND tc.table_schema IN ('b2','b3','b4');"
# 期望：无跨 schema 引用（每个 FK 的引用表与被引用表同 schema）
```

> 角色名以 B4 的功能阶段实现为准；`b2_role` 为示意。验收时以实际角色名为准并记录。

### 2.6 安全门禁（SEC-01 … SEC-20）

逐项执行 `../testing/cases.md` §3 的用例。**关键要求**：

| 用例 | 判据 | 不得接受的做法 |
|---|---|---|
| SEC-01 禁网 | 沙箱内任何外部网络访问失败**且被记录** | "配置里写了禁网"不算通过 |
| SEC-02 只读根 | 写根路径失败 | — |
| SEC-03 非 root | 提权失败 | — |
| SEC-04/05/06 | 内存/时间/输出超限被限制**并归类** | 仅"进程被杀"不算；必须有类别记录 |
| SEC-07 fork bomb | 宿主未受影响，作业被限制 | — |
| SEC-08 进程树清理 | 超时样本执行后 `ps` 无残留 | 仅"进程已退出"不算；须检查**整个进程树** |
| SEC-09 静态高危阻断 | 高危样本 `rejected` 且**未进入沙箱** | 用"跑完发现失败"代替阻断不算 |
| SEC-20 沙箱不可用 | 缺 cgroup v2 时动态评测被拒 | **绝不允许**退化为无沙箱执行 |

**必须保留产物**：每个用例的命令、输入样本、原始输出、结论。**样本必须是真实执行结果，不是断言。**

### 2.7 可重放性门禁

1. 对同一提交连续评测两次。
2. 比较五元摘要、分数、逐项证据、`result_digest`。

**通过判据**：五元摘要相同 → 分数与逐项证据完全一致，`result_digest` 相同。

**若不一致**：必须能指出**是哪一项摘要不同**（这是分项记录摘要的理由），并判断属于"合法差异"（如解释器版本变化）还是缺陷。

### 2.8 资源门禁（RES-01 … RES-05）

```bash
# 容器级
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"

# 精确值（cgroup v2）
docker exec <container> cat /sys/fs/cgroup/memory.current
docker exec <container> cat /sys/fs/cgroup/memory.peak

# 单次沙箱作业峰值（在评测进行中采样作业 cgroup）
cat /sys/fs/cgroup/<job-cgroup>/memory.peak

# 磁盘
df -h
docker system df
du -sh <pgdata> <jetstream-data> <b2-storage> <b3-resources>
```

**通过判据**（见 `../architecture/resource-budget.md`）：

| 组件 | 预算 |
|---|---|
| host | ≤160 MB / 0.35 vCPU |
| PostgreSQL | ≤96 MB / 0.15 vCPU |
| NATS | ≤32 MB / 0.05 vCPU |
| 单并发沙箱作业 | ≤96 MB / 0.45 vCPU |
| 合计 | ≤512 MB / 1.00 vCPU，**保留 ≥128 MB 内存余量** |
| 磁盘 | ≤10 GB，各用途在预算内，保留 ≥1 GB |

**把所有实测值填入 `../architecture/resource-budget.md` §5.4 的结果表**，替换"待测"。

### 2.9 备份与恢复演练

```bash
docker compose exec -T postgres pg_dump -U autograder_platform -d autograder > /tmp/autograder.dump
# 在干净实例上恢复
docker compose exec -T postgres psql -U autograder_platform -d autograder_restore < /tmp/autograder.dump
```

**通过判据**：恢复后三个 schema 的表结构与行数与备份时一致。**必须留下命令与结果记录。**

### 2.10 端到端

按 `../testing/cases.md` §2 执行 E2E-01 … E2E-30。

---

## 3. 结果记录模板（必须如实填写）

| 门禁 | 命令 | 环境 | 结果 | 未执行原因 |
|---|---|---|---|---|
| 契约与文档门禁 | | | | |
| 各模块 lint/test | | | | |
| 迁移 | | | | |
| readiness | | | | |
| G-DB-01/02/05 | | | | |
| SEC-01…20 | | | | |
| 可重放性 | | | | |
| RES-01…05 | | | | |
| 备份恢复 | | | | |
| E2E-01…30 | | | | |

**失败或未执行必须写明**。任何一项失败都使阶段保持 `active`（见 `../testing/acceptance.md` §1）。

---

## 4. 当前的验收状态

**尚未在本 Linux 环境执行任何一项。** 本节不列出"预期通过"的项，只声明事实与下一步：

| 项 | 状态 |
|---|---|
| 环境准备（cgroup v2 / nsjail / 资源） | 未确认 |
| §2.1 契约与文档门禁 | 已在 Windows 通过；Linux 待复跑 |
| §2.2–§2.10 | **全部未执行** |
| 资源账本实测 | **无数据** |

**在这些项目有真实数据之前，不得声称：** 沙箱安全成立、资源账本成立、系统可在 Linux 运行、或架构解耦已被验证。
