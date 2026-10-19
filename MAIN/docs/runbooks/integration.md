# 运行手册：模块组装与集成运行

本手册说明**如何把 B2/B3/B4 三个后端模块组装成一个可运行系统**，以及组装失败时怎么定位；B1 由 Vercel 独立发布。

这是本项目的核心目标所在：**各模块遵守规范即可即插即用**。因此本手册同时是"即插即用"是否成立的检验规程。

---

## 1. 组装模型

```text
B1（Vercel 静态客户端） ── HTTPS API ── Cloudflare Tunnel ── MAIN composition host
                                                           ├── B2
                                                           ├── B3
                                                           └── B4
```

**关键性质**：模块之间**没有代码依赖**。host 通过 Python entry point 发现它们，通过 capability 声明排序，通过契约通信。

---

## 2. 组装前置条件

| 条件 | 说明 |
|---|---|
| 各模块 `module.yaml` 存在且合法 | 通过 `contracts/manifest.schema.json` |
| 各模块 `contracts.lock` 与 MAIN 实时摘要一致 | 版本 + SHA-256 |
| 各模块提供 entry point | 组 `autograder.modules`，值为 `<module>:<factory>` |
| 各模块的 `requires` 能被满足 | 例如 B2 需要 `host`、`b4-auth`、`b3-questions` |
| B1 部署 | 与后端组装独立；Vercel 从 `B1/` 构建静态资源，MAIN host 不挂载 `/app` |

**验证前置条件（无需启动 host）**：

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
.\.venv\Scripts\python.exe -m autograder_host.checks
```

这一步会校验清单合法性、路由与 schema 的唯一归属，以及四份锁与实时摘要的一致性。**组装前必须通过。**

---

## 3. 组装步骤

### 3.1 B1 独立构建

```powershell
cd D:\VScodeProjects\AutoGrader\B1
pnpm install
pnpm build          # 生成 dist/
```

### 3.2 安装三个后端模块（使 entry point 可见）

```powershell
cd D:\VScodeProjects\AutoGrader\B2 ; uv sync --all-groups
cd D:\VScodeProjects\AutoGrader\B3 ; uv sync --all-groups
cd D:\VScodeProjects\AutoGrader\B4 ; uv sync --all-groups
```

> 各模块都依赖 `autograder-platform==0.1.0`，其来源为 `../MAIN/sdk`（见各 `pyproject.toml` 的 `[tool.uv.sources]`）。因此 MAIN 必须与三个模块位于同一父目录下。

### 3.3 启动基础设施

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose up -d postgres nats
docker compose ps          # postgres 必须 healthy
```

### 3.4 启动 host

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose up -d --build host
docker compose logs -f host
```

### 3.5 验证组装

```powershell
curl http://localhost:18000/health/live       # {"status":"ok"}
curl http://localhost:18000/health/ready      # ready + modules 列表
```

**成功标志**：`/health/ready` 的 `modules` 中出现 **B2、B3、B4** 三项，且各自 `healthy: true`。

> **验证陷阱（会让人误报缺陷）**：Windows PowerShell 5.1 的 `Invoke-WebRequest` / `Invoke-RestMethod` 看到 `Content-Type: application/json`（不带 `charset`）时按 ISO-8859-1 解码，于是 B2 健康详情里的中文会显示成 `è¯æµé¾å¯ç¨`。**这是客户端造成的，不是服务端缺陷**——JSON 按 RFC 8259 即 UTF-8，服务端返回的字节是正确的 UTF-8（浏览器也不会错）。要确认请读原始字节再显式按 UTF-8 解码：
>
> ```powershell
> $wc = New-Object System.Net.WebClient
> [System.Text.Encoding]::UTF8.GetString($wc.DownloadData('http://127.0.0.1:18000/health/ready'))
> ```
>
> 不要为了消除这个现象去给响应加 `charset` 或把中文改成英文。

### 3.6 重启前的组装就绪探测

host 一旦启动就长期驻留，于是"现在这套代码还能不能组装起来"平时无人回答，**偏偏在重启那一刻才暴露**——而那是最不适合排查的时机。

`create_app()` 会完整走一遍装载（模块发现与按能力定序、各模块 settings view、路由注册、静态挂载）但**不绑定端口**，因此可以在 host 继续对外服务时安全执行：

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
uv run --no-sync python scripts\probe_host_assembly.py
```

三种结果：

| 输出 | 含义 |
|---|---|
| `host assembled: N route paths` | 可以重启。N 来自 `app.openapi()`，**不是** `app.routes` —— 当前 FastAPI 的 `include_router` 是延迟展开的，读 `app.routes` 只看得到宿主自己的 7 条路径，本探针曾因此报出 `routes from CHG-0011..0015: none (installed code predates them)` 这种**错误归因**（它会把人送去重装，而重装不可能改变答案） |
| `contract vs composition (N declared path(s)):` 段 | **与契约的全量双向比对**：`declared but NOT served` 列出契约已声明但当前组装里没有模块服务的路径；`served but NOT declared` 反向。这一段取代了早先一个**硬编码 marker 列表** —— 那种写法让任何**新声明**的端点都不出现在输出里，读者会从"我认识的七条都在"得出"全部都在" |
| `module readiness:` 段 | 逐模块 `health()`。**这一段才是"重启就绪"的真正判据** —— 组装成功但某模块读不到自己的依赖，不是可重启状态 |
| `host assembly FAILED: ...` | **先修复再重启**，否则重启等同于把服务停机 |
| `not restart-ready: [...]` | 同上；退出码 1 |

探测针对**已安装**的包，也就是真实重启会加载的那一份，所以"改完源码没重装"必然在这里失败——这正是它要拦的情况。退出码 0/1，可直接用于脚本。加 `--no-health` 可在依赖（PostgreSQL/NATS）未启动时只做组装检查。

> **这一段有过一次真实的漏报，值得记住**：探针原先只数路由，于是
> `B5: contract tree unreadable` 与 B2 把 `MAIN/contracts` 解析到虚拟环境内部这类
> **只在已安装形态下出现**的缺陷，除它以外没有任何地方能现形（各模块的单元测试都从工作树运行，
> 那里推导恰好是对的）。逐模块 `health()` 断言就是为此加的。

### 3.7 集成环境变量是可再生产物

`.env.integration` **不是**配置的存放处，而是从 Doppler 渲染出来的构建产物（它 gitignored，
此前是手工维护的，于是"这个部署到底设了什么"没有可评审的答案）。

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
pwsh -File scripts\render_integration_env.ps1
pwsh -File scripts\render_integration_env.ps1 -Config dev_personal -OutFile .env.integration
```

- **基础配置**（DSN、端口、路径、JWT 设置、超时，以及 provider 的三个 bedrock 值
  `APIKEY`/`BASE_URL`/`MODEL`）存放在 Doppler。
- **派生 provider 块**（`B2_COMMENTARY_*` / `B3_AUTHORING_*`，共 8 个变量）由渲染器生成、**不存储** ——
  存它会把同一个 API key 放进三个地方。
- 渲染器里有一处必须写明的**不对称**：B2 取 **base** url（它自己追加 `/chat/completions`），
  B3 取**完整端点**（它逐字 POST）。配错会得到一个"存在但永远失败"的 provider。
- 验证方式：删除该文件后重新渲染，应当**字节完全相同**（实测 SHA-256 相等）。

### 3.8 启动模块自己的进程（worker 与中继）

`docker compose up` 起的是 composition host；**评测 worker 与三个事件中继不在其中**，
它们刻意以独立进程运行。缺了它们，提交会停在 `validated`、发件箱会一直积压
（`attempts = 0` 意味着"从未尝试投递"，与"投递失败"是两种不同的故障）。

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
pwsh -File scripts\run_module_process.ps1 -Module B2 -Entry autograder_b2.worker
pwsh -File scripts\run_module_process.ps1 -Module B3 -Entry autograder_b3.consumer_cli
pwsh -File scripts\run_module_process.ps1 -Module B4 -Entry autograder_b4.ops.messaging -Arguments relay
```

该脚本做的正是宿主在 `module_settings()` 里做的事（ADR-0008）：**保留全部原名**，再把
`<MODULE>_` 前缀变量**覆盖式**地重暴露为无前缀名。两遍都不可省 —— 模块混用两种写法
（B2 既读 `B2_ARTIFACT_ROOT` 又读 `DATABASE_URL`），只做一遍会让同一模块在"宿主内运行"与
"独立运行"之间看到**不同的配置**。

启动后用一条命令核对整条链路：

```powershell
.\.venv\Scripts\python.exe scripts\event_chain_status.py
```

判据：所有 outbox 的 `unpubl` 为 0、死信为 0。

> **`attempts` 不是判据，而且本手册此前把它读反了**（2026-09-20 更正）。它计的是**失败过**的投递：
> B2 新建行就是 `attempts=0`，只有 `_record_failure` 会 `+1`，而 `_mark_published` 只清 `last_error`、
> 保留计数。所以"首次即成功"的健康行**恰恰是 0**。实测 38 行 outbox 中：`attempts=0` 的 23 行
> **全部已发布**、`attempts=1` 的 15 行**也全部已发布**、**没有任何一行带 `last_error`**。
> 原文"`attempts` 为 1（不是 0，也不是持续增长）"因此既不必要也不充分。

`unpubl = 0` 同样是**必要而非充分**的：长时间没有新事件时它一样为 0。要证明发布**仍然可用**，
必须**产生一条事件**并看 `unpubl` 回到 0——2026-09-20 实测：一次真实提交产生 **7 条**新 `b2.outbox`
行（`submission.accepted`、`evaluation.started`、4×`evaluation.progress`、`evaluation.failed`），
**全部发布**、`unpubl` 回到 0、`last_error` 为空。

`b3.event_inbox` 仍会是 0 —— 它需要一次**真实完成的评测**，而那需要 Linux 沙箱；
不得因为发布端全绿就暗示消费端已验证。

### 3.9 改动任何模块之后的收口序列（**顺序不可颠倒**）

**为什么顺序重要（实测，不是提醒）**：宿主进程在启动时就把模块代码载入内存，此后**改源码或重装副本都不会
改变它的行为**。2026-09-20 的实测：宿主 `pid 42120` 启动于 `19:03:08`，而安装副本里 `autograder_b3` 最新的
`.py` 是 `19:20:05` —— 门禁全绿、`probe_host_assembly.py` 报 `restart-ready`，而**线上仍按旧代码回答**：
草稿取包返回 `CHG-0028` 之前的 `409 "a draft version has no evaluation package"`。**重启后同一请求返回 `200`，
唯一变量是进程。** 危险在于它的样子：重启前跑的"真实端口 E2E"会得出**关于旧代码的**结论，与"新代码有 bug"
无法区分。

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN

# 1) 重装四个模块。`--extra modules` 不可省：四个模块正是由该 extra 装进来的，
#    少了它 uv 会判定它们"不再被需要"并全部卸载，把 stale 变成 not installed。
uv sync --all-groups --extra modules `
  --reinstall-package autograder-b2 --reinstall-package autograder-b3 `
  --reinstall-package autograder-b4 --reinstall-package autograder-b5
.\.venv\Scripts\python.exe -c "from autograder_host import checks; checks.check_module_installations()"

# 2) 重启会长期驻留的进程，并跑一次一次性中继（B4 的 relay 是"排空一次"，不是常驻服务）
#    先停掉旧的 host / B2 worker / B3 consumer，再分别起：
pwsh -File scripts\run_host.ps1 -Port 18000
pwsh -File scripts\run_module_process.ps1 -Module B2 -Entry autograder_b2.worker
pwsh -File scripts\run_module_process.ps1 -Module B3 -Entry autograder_b3.consumer_cli
pwsh -File scripts\run_module_process.ps1 -Module B4 -Entry autograder_b4.ops.messaging -Arguments relay

# 3) 先证明"进程晚于它加载的代码"，再做任何真实端口断言（这一步是第 3 节的新增前提）
.\.venv\Scripts\python.exe .runtime\host_freshness.py --port 18000      # 退出码 0=fresh 1=stale 2=说不清

# 4) 重录受影响模块的证据（它会注入该模块 compose.yaml 里声明的 test DSN）
.\.venv\Scripts\python.exe scripts\test_evidence.py --module B2
# 5) 门禁与逐门禁状态
.\.venv\Scripts\python.exe -m autograder_host.checks
.\.venv\Scripts\python.exe scripts/gate_status.py --quiet
# 6) 真实端口 E2E（提交链路 + 出题辅助的真实 provider 往返）
.\.venv\Scripts\python.exe .runtime\e2e_submission.py
.\.venv\Scripts\python.exe .runtime\e2e_authoring_provider.py
```

> **`pwsh` 不在这台机器的 PATH 上**（2026-09-20 实测：`pwsh` → `FileNotFoundError`，`powershell` 可用）。
> 上面沿用本手册既有的 `pwsh` 写法；照跑报"无法识别"时换成 `powershell -File ...` 即可。

**第 4 步为什么必须由这个脚本做**：模块的套件会**按环境变量决定跑多少**。B2 的 conftest 在没有
`B2_TEST_DATABASE_URL`/`DATABASE_URL` 时让所有数据库用例 `skip`，于是同一条命令在同一契约上曾得到
`1293 passed / 9 skipped` 与 `632 passed / 677 skipped` **两种结果，而两者都 `exit=0`**。`test_evidence.py`
现在从模块自己的 `compose.yaml` 派生 test DSN 并把它记进证据物件，门禁也会拒绝"声明了 test 实例却没有
`database`"的记录。

**本机演示不了的三件事**（是环境事实，不是缺陷；不要把它们读成失败）：

| 演示不了 | 原因 |
|---|---|
| 证据包面板的真实渲染 | 库里从未有过一次**完成**的评测（`evaluation_results = 0`），因此没有报告可下钻 |
| **非空**的 AI 参考分 | `advisory_score` 的满分由 `case_evidence` 权重求和填入；没有完成的运行就没有证据 |
| `verify → attest → publish` 完整闭环 | 需要**真的执行**参考解答；Windows 无 nsjail，`ADR-0004` 禁止降级为裸进程 |

---

## 4. 开发场景：未安装模块时的装载

若某个模块尚未 `uv sync`（未注册 entry point），可用环境变量临时指定：

```powershell
$env:AUTOGRADER_MODULES = "autograder_b2.plugin:create_module,autograder_b3.plugin:create_module"
```

格式为 `module:factory`，逗号分隔。**这是开发便利机制，不是交付方式**：正式组装必须依赖 entry point，否则"即插即用"没有被真正验证。

---

## 5. 组装失败诊断矩阵

host 的设计意图是**快速失败并指明原因**。以下按失败信息定位。

| 失败信息 | 原因 | 处置 |
|---|---|---|
| `did not provide a ModuleDescriptor` | 工厂函数未返回带 `descriptor` 的对象 | 检查模块的 `create_module()` 返回类型 |
| `module capability dependency cycle or missing capability` | `requires` 中有未被任何模块 `provides` 的能力，或存在循环 | 按信息中列出的缺失能力名对照各 `module.yaml`；确认提供该能力的模块已装载 |
| `duplicate module routes` | 两个模块声明了同一路由 | 对照各自 `module.yaml` 的 `routes`；路由归属必须唯一 |
| `module.yaml and the runtime descriptor disagree`（`descriptor.contract_version` 或 `routes`） | 该模块**已安装的那份代码**落后于工作区；注册表把声明与运行期描述符对照时发现分歧 | 这是**安装陈旧**，不是声明写错：重装该模块（`uv sync --all-groups --extra modules --inexact --reinstall-package autograder_bN`）。改 `module.yaml` 不能消除它 |
| `<lock>: contract version mismatch` | `contracts.lock` 的版本与 `module.yaml` 不一致 | 同步两者 |
| `<lock>: contract digest mismatch` | 契约已变更但锁未更新 | 按 `../contracts/change-protocol.md` 第 5 步更新**四份**锁 |
| `<lock>: contract digest mismatch`（仅某一个模块） | 该模块被遗漏 | 四份锁必须一起更新 |
| `database schema ... owned by ... and ...` | 两个模块声明拥有同一 schema | schema 归属必须唯一（见 `../database/ownership.md`） |
| `/health/ready` 中 `modules` 为空 | 没有 entry point 被安装 | 执行 §3.2；或临时用 `AUTOGRADER_MODULES` |
| `ready=false` 且指明 `postgres` | 数据库不可达 | 检查容器状态与 `DATABASE_URL` |
| `ready=false` 且指明 `nats` | JetStream 不可达 | 检查容器与 `NATS_URL` |
| Vercel `/app/` 404 但后端正常 | 前端项目根目录或 Vercel rewrite 配置不匹配 | 检查 Vercel Root Directory=`B1` 与 `B1/vercel.json` |

---

## 6. 即插即用验证规程（这是本项目的核心能力，必须实测）

八项判据的状态如下。**证据不是"设计支持"，而是可复现的执行**：
`B5/tests/` 的即插即用一致性套件（`cd B5 ; uv run pytest -q` → 28 passed）与 MAIN 门禁。
B5 本身是 `ADR-0013` 的验收物：它是规范冻结**之后**照着规范写的第一个模块，
因此"无需改 MAIN 代码"这句话有了实证而不是主张。

| 验证 | 步骤 | 期望 | 状态与证据 |
|---|---|---|---|
| **可发现** | 安装一个新模块后启动 host | 该模块出现在 `/health/ready` 与注册表 | **已执行**：`discovered_modules()` → `['B1','B2','B3','B4','B5']`；B4 注册表日志 `manifests=["B1"…"B5"]`、`loaded=["B2","B3","B4","B5"]` |
| **顺序正确** | 打乱安装顺序启动 | capability DAG 保证装载顺序，结果一致 | **已执行**：实测装载序 `['B4','B3','B2','B5']`。另有一个 `requires: b5-conformance` 的 **B0 探针**（按名字排序最前）证明顺序**不是字母序的巧合** |
| **缺失即失败** | 卸载某模块后启动（B2 依赖 `b3-questions`） | 启动**失败**并指明缺失能力 | **已执行**：`ModuleLoadError`，消息含缺失能力名与模块名。**不是**"抛了异常就算"——断言的是具体失败信息 |
| **重复即失败** | 让两个模块声明同一路由 | 启动失败并指明冲突路由 | **已执行**：与真实 B4 的 `/api/v1/auth` 冲突时启动失败 |
| **漂移即失败** | 改一个契约字节后启动 | 失败并指明哪个模块的锁不匹配 | **已执行**：`check_manifests()` 报出具体模块与"锁版本/摘要 ≠ 契约 `info.version`/实时摘要"。这条门禁在本轮**真实拦下过三次**版本漂移（含它自己的一个漏洞修复前的漏报） |
| **无需改 MAIN 代码** | 新增一个遵守规范的模块 | 不改 MAIN 任何代码即可被装载 | **已执行**：B5 只改了 `MAIN/pyproject.toml` 的两行（`modules` extra + `[uv.sources]`）——那是**部署声明**，不是代码；`host/`、`sdk/`、`contracts/` 的装载与校验逻辑一行未改 |
| **无跨模块 import** | 全仓搜索 | 不存在 `import autograder_b2` 之类的跨模块导入 | **已执行**：`B*/src` 对彼此包名的匹配全是 docstring，无一条真实 import |
| **移除即净** | 移除某模块 | 其路由、事件与健康项全部消失，无残留 | **部分执行**：路由与健康项随模块卸载而消失已实测；**静态客户端 B1 的缺失仍是唯一静默的一项**（ADR-0007 已如实记录：B1 不在 capability DAG 内，它不是插件）。未做的是端到端的"卸载后跑完整 E2E" |

**尚未执行的验证（不得表述为已通过）**：

- **真实端口 e2e**：一致性套件用组装后的应用（`create_app()` + `TestClient`）验证端点，**未绑定真实端口**；跨机独立部署完全未验证。
- **HTTP 方法级比对**：B5 的报告比对的是**声明**（契约路径前缀 vs 描述符前缀），不是对运行中的服务逐个探测其方法。因此"契约声明了 GET、实现却只支持 POST"这一类差异**不会被发现**。
- **容器路径**下的组装（见 `phase-01` 的 E1 条目）。

> **这一节是"功能完全解耦"这一目标的判据。** 上面每一项都有可复现的执行证据；**未执行的三项已逐条写明**，
> 在它们完成之前不得声称解耦已被完整验证。

---

## 7. 与文档索引的联动

组装完成后，B4 的运行期注册表（`RQ-B-4-40`）应能报告已装载模块的能力/路由/事件清单。该注册表必须与各模块 `module.yaml` 及 SDK descriptor **三方一致**；不一致视为失败，不允许"以某一方为准"地平滑处理。

对照检查：

```powershell
curl http://localhost:18000/api/v1/modules      # B4 注册表
```

再逐一对照各模块 `module.yaml` 的 `provides` / `requires` / `routes` / `events`。

---

## 8. 停止与回滚

```powershell
cd D:\VScodeProjects\AutoGrader\MAIN
docker compose down            # 保留卷
```

**不做代码级回滚设计**：契约变更是**一次性切换**，不保留旧版本兼容路径。若需要回退某个契约变更，按 `../contracts/change-protocol.md` 第 5 步反向重算摘要并更新四份锁。

**每轮集成后必须确认**：无残留沙箱进程、临时工作区已清理、outbox 无异常积压。
