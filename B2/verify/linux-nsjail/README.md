# Linux nsjail 验收环境（AutoGrader B2 · 大类 5 首项）

本目录提供一个**可复现的 Linux nsjail 执行环境**：宿主是 Windows 时，nsjail 依赖的
Linux 命名空间与 cgroup v2 都不存在（`docs/adr/ADR-0002-sandbox-execution-policy.md`、
`MAIN/docs/adr/ADR-0004-sandbox-technology-selection.md`），因此把**真实**的沙箱验收放进一个
特权 Linux 容器里跑。配套验收清单是 `tests/linux/test_nsjail_acceptance.py`。

> 本文件里的每个结论都来自本机实测，命令与原样输出见 §5「失败尝试记录」与
> `docs/phases/active/` 的阶段记录。**没有实测过的项一律标注"未验证"**。

---

## 1. 实测环境

### 1.1 宿主

| 项 | 实测值 |
|---|---|
| 平台 | Windows 宿主 + Docker Desktop |
| Docker | `docker version` → Server `linux/amd64`，`29.8.0` |
| 现役容器 | `autograder-b2-postgres`（55433）、`autograder-b2-nats`（54223）——本环境**不触碰** |

### 1.2 容器内（`docker run --privileged --cgroupns=host <image>`）

| 项 | 实测值 |
|---|---|
| 发行版 | `Debian GNU/Linux 12 (bookworm)` |
| 内核 | `Linux <cid> 6.18.33.2-microsoft-standard-WSL2 #1 SMP PREEMPT_DYNAMIC ... x86_64` |
| cgroup | 统一层级：`stat -fc %T /sys/fs/cgroup` → `cgroup2fs` |
| cgroup 控制器 | `cat /sys/fs/cgroup/cgroup.controllers` → `cpuset cpu io memory hugetlb pids rdma` |
| 自身 cgroup | `0::/docker/<容器 id>`（`--cgroupns=host` 使容器看到宿主层级） |
| 资源 | `nproc`=32，`MemTotal`≈16 GB（宿主 WSL2 VM 规模） |
| Python | 3.12；依赖由 `B2/uv.lock` 锁定（pytest 8.4.2、SQLAlchemy 2.0.53、psycopg 3.3.5） |
| nsjail | 3.4（源码构建，见 §2）。`nsjail --help` 首行见下方代码块 |

```text
Usage: /usr/local/bin/nsjail [options] -- path_to_command [args]
```

---

## 2. 如何构建

```powershell
docker build -f verify/linux-nsjail/Dockerfile -t autograder-b2-nsjail:local .
```

**两条本机必需的绕行**（Dockerfile 里已带说明并留了覆盖口）：

1. **基础镜像**：本机 Docker Hub 加速器 `mirror.ntruss.com` DNS 不可解析
   （`dial tcp: lookup mirror.ntruss.com: no such host`），无法直接拉 `python:3.12-slim-bookworm`。
   本机实际做法是先拉可访问镜像源再打成本地名：

   ```powershell
   docker pull docker.m.daocloud.io/library/python:3.12-slim-bookworm
   docker tag  docker.m.daocloud.io/library/python:3.12-slim-bookworm python:3.12-slim-bookworm
   ```

   也可以用 `--build-arg BASE_IMAGE=<可访问镜像源>` 覆盖。

2. **Python 依赖索引**：镜像按 `B2/uv.lock` 使用官方 PyPI；需要指定其他索引时传入
   `--build-arg UV_INDEX_URL=<index-url>`。

**nsjail 不是 apt 包**：`apt-cache search nsjail` 在 Debian bookworm 官方源里返回**空**
（只有 `firejail` / `jailkit`）。因此镜像**从源码构建 nsjail 3.4**（tag 钉死），
构建依赖：`bison flex libprotobuf-dev protobuf-compiler libnl-3-dev **libnl-route-3-dev**
libcap-dev libseccomp-dev kconfig-frontends g++ make pkg-config git`。
注意 `libnl-route-3-dev` 必需：缺它 `net.cc` 会因 `<netlink/route/link.h>` 编译失败。

构建层 2 对「clone + 编译」整体重试 5 次：本机容器内直连 `github.com` 不稳定
（`GnuTLS recv error (-110)`），单次失败不应让整次构建作废。层 3 末尾有**构建期自检**
（`nsjail --help` 必须真的能跑），避免产出「构建成功但 nsjail 起不来」的镜像。

---

## 3. 如何运行

### 3.1 推荐：宿主驱动（一个固定参数数组调 docker，容器内跑全部用例）

```powershell
$env:AUTOGRADER_B2_NSJAIL_VIA_DOCKER = '1'
uv run pytest tests/linux/test_nsjail_acceptance.py -v -s
```

驱动逻辑在 `tests/linux/test_nsjail_acceptance.py::_docker_driver_argv`：**固定参数数组**、
`shell=False`、无任何 shell 字符串拼接（ADR-0002 决策 5）。宿主侧直接断言容器内产出的
实测矩阵，因而精度高于"只看整体退出码"。

### 3.2 直接跑容器

```powershell
docker run --rm --privileged --cgroupns=host `
  -e PYTHONPATH=/work/src -e PYTHONDONTWRITEBYTECODE=1 `
  -v "D:/VScodeProjects/AutoGrader/B2:/work" -w /work `
  autograder-b2-nsjail:local `
  python3 -m pytest tests/linux/test_nsjail_acceptance.py -v -s --no-header -p no:cacheprovider
```

### 3.3 真实 runner 验收（把 `src/**` 接进容器）

`tests/linux/test_nsjail_runner_acceptance.py` 导入**真实的** `src/autograder_b2/**`，用生产签名
`run_case(package, submission)` 在真 nsjail 里跑学生代码，并（可选）连真实 PostgreSQL 跑真实编排层。
实测命令（本机实际使用、已验证）：

```powershell
# 1) Linux 运行期依赖（容器内无法访问 PyPI 镜像时用离线交叉安装；装到 gitignored 的挂载目录，不进仓库）
uv pip install --target .pytest-tmp/linux-site --python-platform x86_64-unknown-linux-gnu --python-version 3.12 `
  "fastapi>=0.115,<1" "jsonschema>=4.23,<5" "pyjwt>=2.10,<3" "cryptography>=44,<47" "openpyxl>=3.1,<4" "pydantic>=2.10,<3"

# 2) 临时库（含迁移；宿主侧跑 alembic）
$db = "b2_linuxacc_" + ([guid]::NewGuid().ToString("N").Substring(0,8))
docker exec autograder-b2-postgres psql -U autograder_platform -d postgres -c "CREATE DATABASE `"$db`";"
$env:DATABASE_URL = "postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/$db"
uv run alembic upgrade head

# 3) 容器内跑（注意 --init 收僵尸、--add-host 让容器连宿主库、挂载父目录以让 ../MAIN/contracts 可见）
docker run --rm --init --privileged --cgroupns=host `
  --add-host=host.docker.internal:host-gateway `
  -e "PYTHONPATH=/work/B2/src:/work/B2/.pytest-tmp/linux-site" -e PYTHONDONTWRITEBYTECODE=1 `
  -e "B2_TEST_DATABASE_URL=postgresql+psycopg://autograder_platform:change-me-local-only@host.docker.internal:55433/$db" `
  -v "D:/VScodeProjects/AutoGrader:/work" -w /work/B2 `
  autograder-b2-nsjail:local `
  python3 -m pytest tests/linux/test_nsjail_runner_acceptance.py -v -s --no-header -p no:cacheprovider
```

本轮实测结果：**5 passed, 1 skipped**（i7 在容器内 skip：沙箱可用，拒绝路径需在缺 nsjail 的环境验）。
不连库时 i5 会 **skip 并写明原因**（不伪报通过）；`--init` 是必要的——不加时被杀进程会以僵尸形式留在
容器里（无 init 收尸），会让"超时后无残留"的判定失真。

### 3.4 原生 Linux（已装 nsjail）

```bash
pytest tests/linux/test_nsjail_acceptance.py -v -s
```

非 Linux 宿主上本模块默认 **skip 并附明确原因**，不会伪报通过。

---

## 4. 实测的启动参数矩阵（含失败尝试）

`--privileged` 单独一项**不够**——这是本次验收最重要的环境结论之一。

- **A. 无任何参数**（默认 `docker run`）
  - 命名空间隔离：❌ `clone(...) failed: Operation not permitted`，nsjail exit 255
  - cgroup v2 限额：—（进程根本起不来）
  - 判定：**不可用**
- **B. 仅 `--privileged`**
  - 命名空间隔离：✅
  - cgroup v2 限额：❌ `/sys/fs/cgroup` 不可写。nsjail 报
    `Couldn't write ... 'cgroup.subtree_control': Device or resource busy` →
    `Couldn't setup parent cgroup (cgroupv2)`
  - 判定：**不足**
- **C. `--cap-add=SYS_ADMIN --security-opt seccomp=unconfined`**（带或不带 `--cgroupns=host`）
  - 命名空间隔离：✅
  - cgroup v2 限额：❌ cgroupfs 只读（`touch /sys/fs/cgroup/x` → `Read-only file system`）。
    nsjail 报 `Couldn't initialize cgroup 2 user namespace for pid=N`
  - 判定：**不足**（"最小权限"方向尝试过，未走通 —— 见 §4.1）
- **D. `--privileged --cgroupns=host`**
  - 命名空间隔离：✅
  - cgroup v2 限额：✅ nsjail 成功写入 `memory.max` / `pids.max` / `cpu.max`
  - 判定：**本方案采用**

补充实测：即使 `--privileged`，Docker Desktop 仍在容器内保留了一层 seccomp 过滤器
（`/proc/self/status` → `Seccomp: 2`、`Seccomp_filters: 1`，`CapEff: 000001ffffffffff`）。

### 4.1 与 ADR-0004 的张力：实测结论

ADR-0004 §「遗留风险」第 1 条要求「容器化部署中运行 nsjail 需要额外的权限配置
（通常需要 `--privileged` 或特定 capability/seccomp 放行），这会与『最小权限』原则产生张力
→ 需在 Linux 验收时明确记录所选方案与其风险，不得含糊处理」。本次实测给出的答复是：

1. **张力是真实的，且比 ADR 预想的更紧**：不是"`--privileged` 或等效 capability 二选一"。
   实测的等效 capability 路线（`--cap-add=SYS_ADMIN` + `seccomp=unconfined`）能让 nsjail
   跑起来，但 **cgroupfs 是只读的**，因此 cgroup v2 限额无法建立 —— 而 cgroup v2 限额是
   ADR-0002 决策 7 的硬要求。**结论：`--privileged` 目前不可替代，`--cgroupns=host` 同样必需。**
2. **风险必须显式承担**：`--privileged` 使容器内的 nsjail 拥有对宿主 WSL2 内核的近乎全部能力。
   其安全边界只剩「nsjail 自身的正确性」这一层。缓解措施与边界：
   - 该容器**只**用于验收，不承载服务（不监听端口、不接数据库，与 55433/54223 的现役容器无关）；
   - 生产部署不得照搬本参数：ADR-0004 里 nsjail 的定位是**直接跑在 Linux 后端上**（无 daemon），
     本容器只是"在 Windows 开发机上构造出的 Linux 验收环境"，不是部署形态；
   - 若确需容器化部署，应改为「nsjail 以非 root 用户运行 + 委派 cgroup 子树（`cgroup.subtree_control`
     预先放权）+ seccomp 放行 `CLONE_NEW*`」，本机未走通（见 §3 表 C），须在有 systemd 的真实
     Linux 上重新验证。
3. **本环境不构成"生产可用"的证据**：下表 §6 的 cgroup 内存限制问题说明 WSL2 内核语义与
   mainline 有差异，因此**生产/CI 的 Linux 验收必须在真实 Linux 内核上重跑**。

---

## 5. 失败尝试记录（构建与运行）

### 5.1 构建期

- **源里没有 nsjail**：`apt-cache search nsjail` / `apt-cache policy nsjail` 均返回空
  （Debian bookworm 只有 `firejail` / `jailkit`）。→ 改为源码构建 3.4。
- **首次源码构建**：`net.cc:54:10: fatal error: netlink/route/link.h: No such file or directory`
  → 补 `libnl-route-3-dev`。
- **Dockerfile 首行 `# syntax=docker/dockerfile:1`**：
  `failed to resolve source metadata for docker.io/docker/dockerfile:1`（加速器
  `mirror.ntruss.com` 不可解析）→ 删除该指令（未用到 BuildKit 特性）。
- **层内 `rm -rf /var/lib/apt/lists/*` 后再 `apt-get install`**：
  `E: Unable to locate package libprotobuf32`（`libnl-3-200`、`iproute2`、`procps` 同）
  → 安装前补 `apt-get update`。
- **`apt-get purge --auto-remove <构建依赖>`**：开始
  `Purging configuration files for libpython3.11-minimal` —— `--auto-remove` 会牵连移除
  与 nsjail 无关的包 → 放弃卸载构建依赖（体积不是本环境约束）。
- **`nsjail --version` 作为构建期自检**：`[F] main():334 Couldn't parse cmdline options`，
  exit 255（**nsjail 没有 `--version`**）→ 自检改为 `nsjail --help` 的输出内容判据。
- **容器内 `git clone github.com/google/nsjail`**：
  `fatal: unable to access ...: GnuTLS recv error (-110): The TLS connection was
  non-properly terminated.` → 「clone + 编译」整体重试 5 次。
- **`--cap-add=SYS_ADMIN` 不加 `--cgroupns=host` 时的 cgroup**：
  `Couldn't write ... cgroup.subtree_control: Device or resource busy` → 采用 `--cgroupns=host`。

### 5.2 运行期：nsjail uidmap 的两条互相冲突的约束（逐变体实测）

被囚进程的 uid/gid 由 **第一条 `uidmap` 的 `inside_id`** 决定；而 nsjail 自己要完成 mount
namespace 的挂载，**调用者的 uid 也必须被映射**（否则它在新 user namespace 里没有 root）。
只写一条必然牺牲其中一件事：

- 只写 `99999 -> 99999`：进程 uid 对了，但 nsjail **挂载失败** ——
  `mkdir('/tmp/nsjail.0.root//workspace'): Permission denied` → `Couldn't mount '/workspace'`。
- 只写 `0 -> 0`（或把 0 写在前面）：挂载成功，但进程 uid = **0**（非 root 失效）。
- **`99999 -> 99999` 在前 + `0 -> 0` 在后**：✅ 两者兼得 ——
  `EUID=99999 EGID=99999`，工作区可写，rc=0。

另外：`uidmap` 的 `outside_id` 留空表示"当前 uid"。若 nsjail 以 root 运行，`--user 99999`
会产出 `inside_uid:99999 outside_uid:0`，nsjail 自己会警告
`Process will be UID/EUID=0 in the global user namespace` —— 此时进程在 **ns 内** euid 非 0，
但在**全局命名空间里仍是 uid 0**，即**假非 root**。因此验收断言不能只看 ns 内 `os.geteuid()`，
必须从外层 `/proc/<pid>/status` 复核（`test_04_jailed_process_is_not_root` 就是这么做的，实测外层
`Uid: (99999, 99999, 99999, 99999)`）。

---

## 6. 已知限制

1. **cgroup v2 `memory.max` 在本内核只记账、不杀进程（已实测，影响验收结论正确性）**。
   nsjail 的配置写入是成功的（日志 `Setting 'memory.max' to '67108864'`），被囚进程也确实落在
   `/sys/fs/cgroup/NSJAIL.<pid>`（`memory.max=67108864`），但超限时**没有 OOM kill**。
   为排除 nsjail 的因素，直接在容器里做了**裸 cgroup** 对照：

   ```
   mkdir /sys/fs/cgroup/b2memtest && echo 67108864 > .../memory.max
   echo $$ > .../cgroup.procs ; python3 -c '反复 bytearray(4MiB)'
   → NO_LIMIT_REACHED 800 MiB   (退出码 0，未被杀)
   → memory.peak   = 67108864    (峰值恰好被压在限额)
   → memory.events: max 4873, oom 0, oom_kill 0, oom_group_kill 0
   ```

   即内核**会记账**（`peak` 卡死在 64 MiB、`max` 计数 4873 次）但**不下 OOM 杀手**。
   后果：ADR-0002 决策 7「内存超限」在本环境**不能靠 cgroup 落实**，只能靠 `rlimit_as`
   （实测有效：128 MiB 时限在 112 MiB 处 `MemoryError`）。
   验收清单据此拆成两项：`5a`（rlimit_as，**通过**）与 `5a2`（cgroup 杀进程，**无法执行**，
   标 `xfail(strict=False)`；在 mainline 内核上会 XPASS，即限制恢复生效的信号）。
2. **nsjail 3.4 没有 stdout/stderr 大小选项**（逐字段核对 `config.proto`：无该类字段）。
   输出上限只能由**读取侧**强制，验收项 `5d` 即按此实现并在文档中标明。
3. **本环境是 WSL2 内核，不是 mainline**。cgroup v2 语义差异已见第 1 条。
   生产/CI 的 Linux 验收**必须在真实 Linux 内核上重跑**，本目录只保证"在 Windows 开发机上
   能真实执行 nsjail 并给出可信结论"。
4. **`--privileged` 的权限面很宽**（见 §4.1）。本容器只用于验收，不得照搬为部署形态。
5. **构建依赖留在镜像内**（未卸载）：`apt-get purge --auto-remove` 在本机会牵连移除无关包；
   构建依赖不改变沙箱的权限面，故接受该取舍。镜像约 1.2 GB。
6. **Docker Hub 加速器不可用**：需要本地 `docker tag` 或 `--build-arg BASE_IMAGE=...`（§2）。
7. **`nsjail --help` 是唯一的版本证据**：nsjail 没有 `--version`，验收日志里记录的是 `--help` 首行。

---

## 7. 本目录文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 构建验收镜像：Debian bookworm + Python 3.12 + 源码构建的 nsjail 3.4 + 项目依赖 |
| `README.md` | 本文件：构建/运行方式、实测环境、启动参数矩阵、失败尝试与已知限制 |

验收清单本身在 `tests/linux/test_nsjail_acceptance.py`（自带最小 nsjail 策略，**不导入** runner；
两个集成项 `test_i1_runner_generated_config_is_accepted_by_real_nsjail` /
`test_i2_nsjail_log_separates_diagnostics_from_student_streams` 只读 runner 生成的策略与 argv：前者校准字段名与 seccomp 允许清单，
后者证明 `--log` 把沙箱自有诊断与学生流物理分开——沙箱侧归因的可信边界）。
