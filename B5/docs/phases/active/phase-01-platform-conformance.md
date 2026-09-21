# Phase 01 — 平台一致性审计模块（B5）落地

## 目标与范围

- **目标**：交付 ADR-0013 的验收物——一个在约定冻结**之后**照着约定写成的第五个模块，
  用它证明 PRD §8.6 的判据（"遵守规范的模块，不改 MAIN 代码即可装载"），并把
  `MAIN/docs/runbooks/integration.md` §6 中 8 项"未执行"里属于组合期的那几条变成可复现证据。
- **范围**：`B5/**`（模块、契约锁、文档、一致性套件）+ `MAIN/pyproject.toml` 的两行依赖声明
  （CHG-0010 已裁定）。**不动** host 代码、契约文件、`B1`–`B4`、git 状态。
- **非目标**：运行期模块注册表（属 B4）、报告持久化、自动修复、跨机部署演练。

## 输入与决策

| 输入 | 用途 |
|---|---|
| `MAIN/contracts/openapi/b5.yaml`（CHG-0010 落地；当前版本见 [contracts.lock](../../../contracts.lock)） | 端点与响应形状的唯一权威 |
| `MAIN/docs/adr/ADR-0013-open-module-count.md` | B5 存在的理由与它要关闭的三个硬编码堵点 |
| `MAIN/docs/contracts/changes/CHG-0010-b5-conformance-module.md` | §5 的四项必须被证明的验证 |
| `MAIN/host/src/autograder_host/checks.py` | 治理门禁的**真实**必文件清单（不靠猜） |
| `B3/**` | 结构范本（清单、锁、插件、文档树、测试布局） |

本阶段作出的决策（记录在 [../adr/ADR-0001-read-only-conformance-auditor.md](../../adr/ADR-0001-read-only-conformance-auditor.md)）：

1. B5 只读：无 schema、无迁移、无事件、不写文件；
2. 不做运行期注册表（不越 B4 的 `RQ-B-4-40`）；
3. `requires` 使用既有模块的真实能力名（`b4-auth` / `b3-questions` / `b2-submissions` + `host`），
   使报告描述的是**完整**组合；
4. 认证不含授权分支（契约规定"任何已认证主体可读"）；
5. 保留三个真实依赖（`pyyaml` / `pyjwt` / `cryptography`）并如实记为对约束的偏离。

## 完成条件

| # | 条件 | 结果 |
|---|---|---|
| 1 | `uv run ruff check .` 通过 | 见 §验证记录 |
| 2 | `uv run pytest -q` 全部通过、无 skip | 见 §验证记录 |
| 3 | 真实 loader 装载五个模块且顺序满足 DAG | 见 §验证记录 |
| 4 | 四个负例（缺能力 / 能力环 / 重复路由 / 描述符类型）+ 同名 entry point（host 与 B4 两处）各自有**具体**失败断言 | 见 §验证记录 |
| 5 | `checks.discovered_modules()` 含 B5，`module_schema_owners()` 不含 B5 | 见 §验证记录 |
| 6 | `python -m autograder_host.checks` 三条 ok | 见 §验证记录 |
| 7 | `uv lock`（MAIN）解析通过 | 见 §验证记录 |
| 8 | 文档集合满足 `check_module_governance()` | 见 §验证记录 |

## 实际变更

**新增（`B5/`）**

- 模块：`pyproject.toml`、`module.yaml`、`contracts.lock`、`.env.example`、`.gitignore`
- 源码：`src/autograder_b5/{__init__,config,errors,contracts,auth,conformance,api,plugin}.py`
- 套件：`tests/{conftest,_probe_modules,test_composition_load,test_load_failures,test_conformance_endpoint,test_conformance_views,test_governance_discovery}.py`
- 文档：`AGENTS.md`、`README.md`、`docs/**`（规范要求的全部文件）

**修改（`MAIN/`，仅两行，CHG-0010 裁定）**

- `[project.optional-dependencies] modules` 追加 `autograder-b5==0.1.0`
- `[tool.uv.sources]` 追加 `autograder-b5 = { path = "../B5" }`

**未修改**：host 代码、`MAIN/contracts/**`、任何 `contracts.lock`（B5 的锁是新增）、`B1`–`B4`、git 状态。

## 验证记录

环境：Windows + PowerShell + `uv 0.10.2` + Python 3.12（仓库内 `.venv`）。集成环境变量取自
`MAIN/.env.integration`（按 `MAIN/scripts/run_host.ps1` 的方式载入进程）。

### 1. 本模块（`B5/`）

```text
cd D:\VScodeProjects\AutoGrader\B5
uv sync                          → 退出码 0（61 packages；生产依赖 + dev 组）
uv run ruff check .              → All checks passed!
uv run pytest -q                 → 28 passed, 2 warnings in 4.54s（退出码 0，无 skip）
```

两条 warning 来自第三方（`fastapi.testclient` 对 `httpx` 的弃用提示、`starlette` 的
`anyio.abc.BlockingPortal` 别名），与本模块代码无关，未处理。

### 2. 跨模块（`MAIN/`）

```text
cd D:\VScodeProjects\AutoGrader\MAIN
uv lock                          → Resolved 63 packages；Added autograder-b5 v0.1.0
uv sync --all-groups --extra modules --inexact --reinstall-package autograder-b{2,3,4,5}
                                 → ~ autograder-b5==0.1.0 (from file:///D:/VScodeProjects/AutoGrader/B5)
.\.venv\Scripts\python.exe -m autograder_host.checks
  → contracts, manifests and requirements: ok (4ad48fbbd710fbb6e1e3554d9806e4a4c174ab23e6fa8d8dc67d0e26a982d454)
  → module documentation governance and links: ok
  → migration heads and local ports agree with compose: ok
  （退出码 0）
.\.venv\Scripts\python.exe -m pytest -q   → 1 passed（需先载入 .env.integration，见 §3）
```

`--inexact` 是有意加的：MAIN 的 `uv.lock` 不知道 `autograder-host`（它是 `MAIN/host` 的独立
发行版，早期单独 `uv sync` 装进去的），默认的精确同步会把它从**正在服务的**宿主所用的虚拟环境里
删掉。`--inexact` 只增不删，效果是"把 B5 装进去"，而不是"按锁重造环境"。

### 3. 发现式治理与真实组合（CHG-0010 §5.1 / §5.2 / §5.3）

载入 `MAIN/.env.integration`（与 `MAIN/scripts/run_host.ps1` 相同的方式）后执行：

```text
MAIN\.venv\Scripts\python.exe -c "... from autograder_host import checks; print(checks.discovered_modules()); print(checks.module_schema_owners())"
  → ['B1', 'B2', 'B3', 'B4', 'B5']
  → ['B2', 'B3', 'B4']                     ← 不含 B5（它没有 schema）

MAIN\.venv\Scripts\python.exe -c "... from autograder_host.main import _load_modules; print([m.descriptor.module for m in _load_modules()])"
  → ['B4', 'B3', 'B2', 'B5']
```

B4 自己的注册表在同一次装载里打印：

```text
registry.validated  manifests=["B1","B2","B3","B4","B5"]  loaded=["B2","B3","B4","B5"]
```

即：**五个项目模块中四个是插件**（B1 是宿主提供的静态客户端，按 ADR-0007 不在 capability DAG 内），
装载顺序 `B4 → B3 → B2 → B5` 由 `requires` 决定，B5 排在最后。

### 4. 负例（每个都有**具体**失败断言）

| 负例 | 驱动方式 | 断言的失败 |
|---|---|---|
| 缺能力 | **真实 `_load_modules()`**（探针 `B99` 追加进真实组合） | `ModuleLoadError`，消息含 `b98-nothing-provides` 与 `B99` |
| 能力环 | **真实 `_load_modules()`**（两个探针分别来自两个模块） | `ModuleLoadError`，消息含 `B96`/`B97` 与两条能力名 |
| 重复路由前缀 | **真实 `_load_modules()`**（探针声明 B4 已占的 `/api/v1/auth`） | `ModuleLoadError`：`duplicate module routes` + 该前缀 |
| 描述符类型错 | **真实 `_load_modules()`** | `ModuleLoadError`：`did not provide a ModuleDescriptor` |
| 同名 entry point（host 侧） | **真实 `_load_modules()`**（两次声明真实 B4 工厂） | `ModuleLoadError`：`duplicate module entry point name` |
| 同名 entry point（B4 侧） | **直接调用** `autograder_b4.registry.resolve_declared_modules()` | `RegistryError`：`declared twice` |

**诚实边界**：最后一条**没有**经过 host loader——它的目的就是断言"另一处也失败"，因此必须调用
另一处。三视图比对（`test_conformance_views.py`）的 5 个用例也**不是** loader 级证据：
它们直接构造契约目录与 descriptor 调用 `build_report()`，属单元层，证明的是判定规则本身
（含两个反例：宿主路由不算差异、全路径落在前缀下不算差异）。

### 5. 契约版本在本次工作中被推进了三次

`0.5.0 → 0.6.0 → 0.7.0`（CHG-0011，由 MAIN 侧在同一工作区并行推进）。B5 **没有任何地方写死版本**：
`module.yaml` / descriptor / [contracts.lock](../../../contracts.lock) 由契约所有者按流程同步，
文档一律引用文件而非字面值（`module-doc-spec` 铁律一）。上面所有验证都是针对当时的实时版本
（`0.7.0`，摘要 `4ad48fbbd710…`）跑出来的。

## 风险与遗留项

### A. 规范不足（**这是本次最有价值的发现**）

1. **需求编号空间没有随模块数量一起开放。**
   `checks.py` 的 `_REQUIREMENT = r"`RQ-B-[0-4]-\d{2}`"` 与
   `check_requirement_baseline()` 里固定的 32 条，都不承认第五个模块：`RQ-B-5-xx` 会被
   全部门禁当作不存在，而课程 32 条需求也没有 B5 的所有者。ADR-0013 打开了 `manifest.schema.json`
   的模块名 pattern，却没有打开需求编号。**后果**：B5 的需求权威只能是 ADR/CHG 编号，
   `docs/requirements/traceability.md` 无法提供一张真正的 `RQ-B-*` 表（已如实说明，未编造编号）。
   **建议**（属 MAIN 的决定，不在本模块权限内）：要么扩展编号空间，要么给团队轨道一条 `RQ-T-*`
   编号（ADR-0009 已有该轨道）。

2. **`MAIN/scripts/relock_contracts.py` 仍写死 `("B1","B2","B3","B4")`。**
   ADR-0013 决策 3 把 `checks.py` 的字面量列表改成了发现式，但这个脚本的同名字面量被漏掉了。
   **后果**：契约变更时，它算得出实时摘要，却**写不到第五把锁**；本仓库五把锁之所以一致，
   是因为契约所有者手工同步了 B5 的锁（本次会话中版本推进三次，B5 的锁随之被同步三次）。
   这是同一个根因（模块列表字面量）的**第四次**出现，值得单独修：脚本应改为
   `glob("B*/contracts.lock")`。

### B. 与任务约束的偏离（如实记录，未掩盖）

3. **新增了三个生产依赖**：`pyyaml`、`pyjwt`、`cryptography`。任务要求"不新增生产依赖"，
   而这两件事无法同时满足：
   - 视图 1 必须解析 `MAIN/contracts/openapi/*.yaml`——用正则解析结构化契约是被禁止的脆弱做法，
     且 MAIN 的门禁用同一解析器读同一批文件；
   - 契约声明 `security: bearerAuth` 与 `401`——实现它必须有 Ed25519 验签能力。
   三者都是项目**已有**的库（B2 用 `pyjwt`+`cryptography` 做同一件事，B4 生产依赖含 `pyyaml`）。
   理由与备选方案见 [ADR-0001](../../adr/ADR-0001-read-only-conformance-auditor.md) §决策 4。

### C. 环境与流程风险

4. **一致性套件依赖整棵树自洽**。它在导入期构造**真实**宿主应用，因此要求：五把锁与实时摘要一致、
   各模块 descriptor 与清单一致、兄弟模块可导入。并行开发期间这三条都可能瞬时为假
   （本次会话中先后遇到：B2 descriptor 落后一个版本、B3 `auth.py` 语法错误、MAIN venv 里
   模块副本过期）。这不是套件的缺陷（它审计的正是这种一致性），但**必须知道**：
   红灯先看是不是自己的问题。
5. **MAIN 的虚拟环境把模块装成"副本"**，源码改动后副本过期。MAIN 新增的
   `check_module_installations()` 正是为此；B5 在 MAIN 侧每次改动后需要
   `uv sync --reinstall-package autograder-b5`。B5 **自己的** dev 环境把兄弟模块装成
   editable，因此套件验证的是工作区源码而不是快照——这是刻意的选择。
6. **MAIN 的 `pytest` 需要集成环境变量**：`autograder_host.main` 在导入期就调用 `create_app()`，
   而 B4 的路由要求 `DATABASE_URL`。未载入 `MAIN/.env.integration` 时 `pytest` 在收集期失败
   （`autograder_b4.config.ConfigurationError: DATABASE_URL is required`）。这与 B5 无关
   （B5 的配置项全部有默认值），但会让"照抄命令"的人困惑，已在
   [../../runbooks/troubleshooting.md](../../runbooks/troubleshooting.md) 记录。

### D. 未验证项

7. 真实端口上的 e2e（uvicorn + 真实 B4 登录令牌）、跨机独立部署、方法级（HTTP method）比对，
   均**未执行**，原因见 [../../testing/strategy.md](../../testing/strategy.md) §6 与
   [../../requirements/acceptance.md](../../requirements/acceptance.md) §5。
8. 报告对"运行中应用实际服务的路径"只取路由表，不发起真实请求探测——它比对的是**声明**与
   **契约**，不是"某个路径真的能返回 200"。这一点在 [../../architecture/models.md](../../architecture/models.md)
   §3 的判定表中已明确。

### E. 一条由本套件自己发现的真实缺陷

9. 能力环负例的第一版把两个探针放在**同一个模块**里，于是 `AUTOGRADER_MODULES` 产生了两个
   **同名**声明（该机制以模块路径为名），重复名检查先于环检测触发——测试"通过"了，但验证的是
   另一个失败。已拆成两个探针模块（`tests/_probe_cycle_alpha.py` / `_probe_cycle_beta.py`）。
   记录在此：它说明"负例也会说谎"，只断言"抛了异常"远远不够，必须断言**是哪一种**失败。

## 文档影响检查

| 文档 | 是否受影响 | 说明 |
|---|---|---|
| `B5/docs/**` | 是 | 本阶段全量建立 |
| `B5/module.yaml` / descriptor | 是 | 同一次变更，两处字段必须一致（门禁强制） |
| `B5/contracts.lock` | 是 | 新增；摘要来自 `relock_contracts.py` 的计算结果 |
| `MAIN/contracts/**` | 否 | B5 不是契约作者；`b5.yaml` 由 CHG-0010 先行落地 |
| `MAIN/docs/**` | 否 | 不在本任务的文件所有权范围内；B5 只在文档中引用它们 |
| `B1`–`B4` | 否 | 未改动任何文件 |
| `MAIN/docs/runbooks/integration.md` §6 的 8 项状态 | 否（**建议**） | 本阶段为其中若干项提供了可复现证据，但该表属 MAIN 所有；更新它需要 MAIN 侧的决定 |
