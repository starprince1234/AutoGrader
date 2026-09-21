# Phase 02 AI 能力激活与集成缺陷修复

- **状态**：**active**（未满足归档门禁：下列"未执行"项尚未清空）
- **范围**：把 `ADR-0009` 裁决的 AI 能力从"契约已声明、代码已实现、**无人接通**"推进到**真实可调用**；
  修复本轮暴露的一类**只在已安装形态下才显形**的集成缺陷；把配置收敛到可管理的权威来源；
  为 `CHG-0014`/`CHG-0015` 的四个端点与 `RQ-T-30` 的落地腾出可施工的基线。
- **归档判据**：本文件 §6 的未执行项全部有真实读数；`CHG-0018`/`CHG-0019` 的实现侧断言全部执行。

---

## 1. 目标与完成条件

| # | 条件 | 达成 |
|---|---|---|
| 1 | 集成环境起来：容器、迁移到 head、MAIN 门禁 | ✅ §3.1 |
| 2 | AI provider 真实可用，且**两条轨都实测调用成功** | ✅ §3.3 |
| 3 | 部署根解析在**已安装形态**下正确（B2/B4/B5） | ✅ §3.2 |
| 4 | `probe_host_assembly.py` 给出准确诊断而不是误报 | ✅ §3.2 |
| 5 | 三个模块的 outbox 积压清零、无死信 | ✅ §3.4 |
| 6 | 配置有单一权威来源且可重现渲染 | ✅ §3.5 |
| 7 | `CHG-0014`/`0015` 四个端点的实现 | ⏳ 进行中（另派工作包；三个路径已在其源码中注册，lint 干净，正在跑套件） |
| 8 | B1 消费新契约面（评语轨、评分参考、隔离区、证据包、热力图） | ✅ 已完成，见 §3.9 |
| 9 | `CHG-0018`（`RQ-T-30`）的实现侧验证 5 条 | ❌ 未执行（B1 的展示侧已实现并有反向断言；B2 的生成侧未实现） |
| 10 | `ADR-0015` / `CHG-0019` 的参考解答验证编排 | ❌ 仅裁决与变更记录，未实现 |
| 11 | Linux 沙箱验收 | ❌ 本环境不可能（Windows） |
| 12 | 端到端（真实端口、走 B1 界面） | ❌ 待 B2 落地后统一执行（`MAIN/.runtime/acceptance_full.py` 已就绪） |

---

## 2. 本轮修复的真实缺陷

### 2.1 「部署根解析」在已安装形态下全部失效（严重，阻断 B2 的核心路径）

**现象**：`/health/ready` 报 `B5: contract tree unreadable (ContractTreeUnavailable)`，
`ready=false`。

**根因**：三个模块各自用 `Path(__file__).resolve().parents[N]` 推导"仓库根"，而
**只有 host 是 editable 安装**：B2/B4/B5 是普通 wheel 装进 `site-packages`，于是
`parents[N]` 落在虚拟环境里。实测：

```text
autograder_host     -> D:\VScodeProjects\AutoGrader\MAIN\host\src\autograder_host\__init__.py   (editable)
autograder_b2       -> D:\VScodeProjects\AutoGrader\MAIN\.venv\Lib\site-packages\autograder_b2\__init__.py
B2 MAIN_CONTRACTS_ROOT = ...\MAIN\.venv\MAIN\contracts   readable = False
B5 DEFAULT_CONTRACTS_DIR = ...\MAIN\.venv\MAIN\contracts readable = False
B4 default module root  = ...\MAIN\.venv
```

**为什么此前没被发现**：每个模块的单元测试都**从工作树**运行，那里 `parents[N]` 恰好正确；
而只在集成运行时暴露 —— 而集成端到端从未跑完（`phase-01 §12.15` 第 9 项）。

**影响不是"B5 少一个功能"**：B2 的 `contract_schema_errors` 有三个运行期消费者
（`intake/service.py:409` 的**每次提交**、`evaluation/report.py:339,346` 的每次报告、
`events/payload.py:329,350` 的事件），在部署形态下**全部会抛
`ContractUnavailableError`**。即"契约是唯一权威"这条设计在真实部署里是断的。

**修法**（收敛到单一实现，而不是补三处记忆）：
`MAIN/sdk/src/autograder_platform/contracts.py` 新增 `deployment_root()`，两个候选**各自被校验**
（`B4_MODULE_ROOT` 环境变量、源码树推导），都不成立时返回 `None` 让调用方响亮失败；
B2/B4/B5 改为调用它。B1 不涉及。

**实测（修复后，加载真实 `.env.integration`）**：

```text
deployment_root() = D:\VScodeProjects\AutoGrader
B2 contracts root = D:\VScodeProjects\AutoGrader\MAIN\contracts   True
B2 lock           = D:\VScodeProjects\AutoGrader\B2\contracts.lock True
B5 contracts root = D:\VScodeProjects\AutoGrader\MAIN\contracts   True
B4 module root    = D:\VScodeProjects\AutoGrader
```

### 2.2 `probe_host_assembly.py` 的读法失效，并给出**错误归因**

**现象**：探针报 `host assembled: 7 route paths` 且
`routes from CHG-0011..0015: none (installed code predates them)`。

**根因**：当前 FastAPI（实测 **0.141.1**）的 `include_router` 是**延迟展开**的：
模块路由以 `_IncludedRouter` 占位对象存在，只在构建 schema 时展平。探针读 `app.routes`
因而只看得到 host 自己的 7 条路径。

**危害不是"少一行输出"**：它给出的那句 `none (installed code predates them)` 是**错误归因**，
会把读者送去重装——而重装**不可能**改变这个答案。这与 `phase-01 §12.11` 记录的
"我最初的归因是错的，而错误归因会让人去做一件无效的事"是同一类。

**修法**：改读 `app.openapi()["paths"]`（强制展平），并**增加逐模块 `health()` 断言**——
后者才是"重启就绪"的真正判据，也正是 §2.1 那类缺陷唯一能现形的地方。

**实测（修复后）**：

```text
host assembled: 42 route paths (from the OpenAPI schema, which flattens routers)
  static mounts: ['/app']
  health routes: ['/health/live', '/health/ready']
  routes from CHG-0011..0015: ['/api/v1/evaluation-runs/{evaluation_run_id}/commentary',
    '/api/v1/platform/conformance', '/api/v1/questions/{question_id}/authoring-assistance',
    '/api/v1/questions/{question_id}/diagnostics']
  NOT REGISTERED: ['/intake', '/appeal-pack', '/quality-summary']

module readiness:
  ok   B4 / ok   B3 / ok   B2 / ok   B5: 44 contract paths under ...\MAIN\contracts
restart-ready: every loaded module reports healthy
```

`NOT REGISTERED` 三项与 `CHG-0014`/`CHG-0015` 尚未实现的三个端点**逐条吻合** —— 探针现在
给出的是可执行的诊断，而不是一句会误导人的结论。

### 2.3 B3 的出题辅助超时**不可配置**

`Settings.authoring_timeout_seconds` 字段一直存在且默认为 `20.0`，但 `from_env` **从不读它**。
对一个推理模型这是真实缺口（实测单次建议调用耗时 **15.0 s**，接近默认上限）。
已在 `from_env` 中接入 `B3_AUTHORING_TIMEOUT_SECONDS`，并对非法值**在加载期失败**
（而不是在第一次模型调用时才炸）。

### 2.4 `run_module_process.ps1` 与 host 的配置视图语义**不一致**（本轮新引入，当场发现）新写的模块进程启动器最初把 `<MODULE>_` 变量"翻译"为无前缀名，却**丢掉了带前缀的原名**，
于是 B2 worker 报 `B2_ARTIFACT_ROOT is required`。

根因是它没有完整镜像 host 的 `module_settings()`：host 是"**保留全部原名** + 再叠加去前缀名"，
脚本只做了后一半。**这会让同一个模块在"宿主内运行"与"独立运行"之间看到不同配置** ——
是那种只会在生产里显形的差异。已修正为与 host 逐字一致，并在注释里写明第一遍不可省。

### 2.5 **整条评测链路 100% 不可用**，以及我第一次给出的根因是错的

这是本阶段最重要的一条，也是**只有真正跑一次端到端才会发现**的那类缺陷 —— `phase-01`
从未执行过端到端（§12.15 第 9 项），所以它一直躺在那里。

**发现路径**：我按 goal 要求真跑了一次提交（`POST /api/v1/submissions`，见 §3.8）。提交被受理，
worker 取到并处理，然后**每一次评测都在取包阶段失败**：

```text
run transitions (newest first):
  failed | 评测包不可获取或不可解析：本次评测未能执行，不是学生代码的问题。
```

直接探 B3 的 internal 端点（服务令牌**核对一致**，所以不是认证问题）：

```text
GET /api/v1/question-versions/{id}/evaluation-package -> 409
  "the compiled package no longer matches the digest recorded at publish time;
   protected content changed after publication"
```

`load_evaluation_package` **每次重新编译**并与记录值比较 —— 所以这个 409 说的是"重编译的结果与
记录不符"，而它给出的理由（"受保护内容在发布后被改动"）**只是其中一种可能**。

#### 2.5.1 我给出的第一个根因是错的，如实记录

我第一次的判断是：`created_at`（= `published_at`）参与摘要，而它从 `datetime.now(UTC)` 的
**微秒**写进 `timestamptz(3)` 列后被**毫秒**四舍五入，于是"发布时用 `.914237`、取包时用 `.914000`
→ 摘要必然不同"。听起来完整，**而且它解释不了数据**。

**推翻它的测量**（`MAIN/.runtime/verify_root_cause.py`，对三个版本用**修复前**的规则重算，
与 dry-run 打印的修复前记录值比对）：

| 版本 | `to_wire()` 里的 `created_at` | 修复前记录值 | 旧规则重算值 | 匹配 |
|---|---|---|---|---|
| `53f8ac1a`（11:58 发布） | `2026-09-18T11:58:16.245Z` | `4f3b4bb8…` | `6e01896b…` | **否** |
| `0fdbb68f`（11:18 发布） | `2026-09-18T11:18:02.509Z` | `f7757794…` | `4ded35e2…` | **否** |
| `c7b989c0`（12:52 发布） | `2026-09-18T12:52:49.106Z` | `8c49bfdc…` | `8c49bfdc…` | **是** |

两件事由此确定：

1. **`to_wire()` 把时间戳渲染成毫秒**（`.245Z`、`.509Z`、`.106Z`）。所以"微秒 vs 毫秒"的差异
   **在序列化时就消失了**，它对摘要**从来没有**影响。我那个"必发"的推理在第一步就已经不成立。
2. 真正的情形是：**更早发布的那两个版本，用任何规则都复现不出记录值；而更晚发布的那一个，
   连旧规则都能复现。** 也就是说，**在 11:58 与 12:52 之间有一次改变了编译输出的代码变更**，
   它让此前发布的版本全部失效。

**还有一个必须承认的方法论问题**：我随后"验证"修复的方式是"重算并写回，然后取包成功"——
而写回的值正是当前代码算出的值，取包比较的也是当前代码算出的值，**它们相符是构造上必然的**。
那个"6/6 成功"在任何规则下都会成功，**它不构成因果证据**。

#### 2.5.2 真正的结论（结构性，比原判断重要）

`load_evaluation_package` 的设计是**重编译 + 与记录值比较**。这让 `package_digest` 实际证明的是
**"内容 + 编译代码"**，而不是"内容"。后果：

- **任何改变编译输出的代码变更，都会静默地让所有已发布版本失效**；
- 而失败信息说"**受保护内容在发布后被改动**"——它把读者指向错误的对象（内容），
  而真实原因是**代码演进**；
- 于是"不可变性"由一个**重算并比较**的动作来证明，而重算依赖代码版本，这件事本身与
  "不可变"是矛盾的。

正确的方向（**本轮只记录，不擅自实施**）：不可变性应当由**存储**保证——把编译产物（或其规范字节）
存下来并原样服务，而不是每次重算。这样"发布后内容被改动"仍可由 `content_digest` 检出，
而编译器演进不再使历史失效。

> **已裁决（2026-09-20）**：[ADR-0016](../../adr/ADR-0016-package-digest-freezes-the-artifact.md)
> 采纳上述方向，并写明四个被否决的备选方案（重算改写、冻结编译器输出、给摘要加版本、
> 只比 `content_digest`）及各自的否决原因。**实施排在当前集成窗口之后**——见 §3.24。

#### 2.5.3 `created_at` 的作用域修正：保留，但它的性质要说清

我确实改了摘要规则（`DIGEST_EXCLUDED_KEYS = ("package_digest", "created_at")`），而且这个改动
**本身是对的**，理由是两条独立的、与本次事故无关的证据：

- 契约对该字段的原话是 "Canonical digest of the package **content**"，而 `created_at` 是元数据；
- B2 的 `result_digest` 早就有一份显式的易变字段表
  （`VOLATILE_REPORT_FIELDS = ("generated_at", "result_digest", "evaluation_run_id")`），
  把时间戳排除在外 —— 同一个模式在本系统里一处做对、一处做错。

**但必须写明：它不是那 103 个版本失效的原因。** 把一次"重算写回"的数据修复当成"证明了根因"，
是我在 §2.5.1 里已经承认的错误。规则修正与数据修复是两件事：前者是对齐契约，后者是恢复服务。

只影响 B3；B2 **只读取** `package_digest`、从不重算（`integration/bindings.py`），因此无需同步：

| 文件 | 改动 |
|---|---|
| `compile/canonical.py` | `DIGEST_EXCLUDED_KEY`（单键）→ `DIGEST_EXCLUDED_KEYS = ("package_digest", "created_at")`，`digest_without` 改为接受多键 |
| `compile/compiler.py` | `compute_package_digest` 的文档；模块 docstring 里"determinism"一条的理由；`created_at == published_at` 断言的**理由**（它保留，但不再是摘要稳定性的依据，而是"包描述的那次冻结确实发生过"） |

**数据修复**：无论摘要规则如何，那 103 条记录值都与"用当前代码重编译"的结果不符（§2.5.1 已测），
所以必须重算写回。而 published 行被不可变触发器保护
（`tg_question_versions_guard` 明确拒绝 `package_digest` 变更，SQLSTATE `23001`）。
新增 `scripts/repair_package_digests.py`：dry-run 优先，`--apply` 才写；使用**平台（迁移）身份**
（只有它拥有表、能解除触发器）；只改 `package_digest` 一列；`ALTER TABLE … DISABLE TRIGGER`
到重新 `ENABLE` 包在同一个 `try/finally` 里。

**它刻意不是 alembic 迁移**：重算需要编译器，把编译器 import 进迁移会把今天的语义冻进一个
必须在代码演化之后仍能运行的文件里。

**实测**：

```text
published versions: 103
stale digests: 103            ← 100% 与重编译不符
rewrote 103 package digests; immutability trigger re-enabled
re-run dry run: stale digests: 0        ← 幂等
pg_trigger.tgenabled = O                ← 不可变性已恢复

load_evaluation_package（B3 自己的取包路径，随机 6 个已发布版本）:
  packages served: 6/6
```

**这组读数能证明什么、不能证明什么，必须分清**：它证明"服务恢复了"与"重算是幂等的"；
它**不能**证明 `created_at` 的作用域修正是原因 —— 因为写回的值就是当前代码算出的值，
比较的也是当前代码算出的值，相符是构造上的必然（§2.5.1 已承认）。**因果证据是 §2.5.1 那张表。**

**变异证明**（`MAIN/.runtime/prove_digest_rule.py`，同一份包内容、只改时间戳字符串的小数位）：

```text
OLD rule (only 'package_digest' excluded):
  publish-time digest : 3dfcb437c49f566a027f4842…
  fetch-time  digest : e16550e471dbdd92c29a0cb3…
  differ              : True
NEW rule ('package_digest' + 'created_at' excluded):
  publish-time digest : 4d6a206c5c5b96e0afb5f3da…
  fetch-time  digest : 4d6a206c5c5b96e0afb5f3da…
  differ              : False
content still matters : True    <- 摘要仍然覆盖内容（没有被改成无意义的值）
```

**这份证明展示的是"潜在敏感性"，不是本次事故**：它喂进去的是两个**字符串**，而
`to_wire()` 实际渲染时间戳时只到毫秒（§2.5.1 实测 `.245Z`）。所以旧规则虽然"会"对亚毫秒字符串
敏感，那个敏感性在真实数据上**从未触发**。把这条证明当成事故的解释，是 §2.5.1 记录的那个错误的来源。

**旁证：同一个模式在本系统里已经有了正确的实现。** B2 的 `result_digest` 有一份**显式的易变字段表**：

```python
VOLATILE_REPORT_FIELDS = ("generated_at", "result_digest", "evaluation_run_id")
VOLATILE_CASE_FIELDS = ("duration_ms", "resource")
```

`generated_at` 被排除。所以作用域修正**不是引入一个新概念**，而是让 B3 与 B2 已有的做法对齐 ——
同一件事一处做对了、一处做错了。这与"它是那 103 个版本失效的原因"是两回事，两者都要说清。

**测试缺口，以及我第一版守卫也是无效的**：`test_adapters.py` 的确定性测试用**同一个**
`created_at` 编译两次（必然相同），集成夹具的 `PUBLISHED_AT` 又定为
`datetime(2026, 9, 15, 8, 0, tzinfo=UTC)`（微秒为 0）—— 所以"摘要对 `created_at` 敏感"这件事
从来没有被任何测试触及。

我补的第一版守卫用 `microsecond=123_456` 与 `123_000`，**它也是无效的**：这两个值经
`to_wire()` 渲染后都是 `.123`，在旧规则下同样通过。**变异测试当场拆穿了它** ——
把规则改回旧版后它仍然绿（`MAIN/.runtime/mutate_digest_rule.py`）。这正是本项目反复记录的
"断言写得太弱，等于没有断言"。

修正后的守卫改用**相差整整一毫秒**的两个值（`123_000` 与 `124_000`），于是它真的能失败：

```text
baseline (fixed rule)  : 2 passed
mutated  (old rule)    : 1 failed, 1 passed     ← test_the_package_digest_does_not_depend_on_created_at 红
restored               : 2 passed
```

`test_publishing_with_a_microsecond_clock_survives_the_database_round_trip`（`test_versions.py`）
保留，但它的文档字符串已改写成**如实**的说法：它走的是"发布 → DB 往返 → 取包"这条路
（这条路曾对每个题目都 409，值得有测试），但它**不**隔离摘要规则。

`ruff check .` 在改动后为 `All checks passed!`；B3 套件 364 → **366 passed**（两条新测试）。

**尚未完成的验证**：host 重启后经真实 HTTP 的取包与完整评测。当前 host 进程内的 B3 是旧代码，
而重启 host 需要先重装 B2（它的 descriptor 仍是 0.12.0 而 `module.yaml` 已是 0.13.0）——
B2 正在被另一个工作包写入，**故意不在中途重装**。这条记在 §4 未执行项里。

---

## 3. 验证记录（真实命令与读数）

### 3.1 环境与门禁

```text
docker: postgres 55432 (main) / 55433 (b2) / 55434 (b3) / 55435 (b4)，nats 54222/54223/54224
迁移：b2 0009→0012，b3 0003→0004，b4 0002（已最新）
集成库播种数据完好：b3.questions=106，published versions=103，b4.users=4

MAIN 门禁：14/14 PASS
contracts, manifests and requirements: ok (32b0fa8794aa...)   ← 变更前
```

契约升级后（`CHG-0018`，0.13.0）：`12/14`，两条红门禁的**唯一**原因是 B2 未重装
（`descriptor.contract_version='0.12.0'` vs `module.yaml '0.13.0'`），
且 B2 的包在工作树外有代理正在写入，**故意不在中途重装**（那会把半成品装进 venv）。

### 3.2 部署根与探针

见 §2.1 / §2.2 的实测块。

### 3.3 AI provider 真实调用（两条轨）

配置来源：Doppler 项目 `autograder` / config `dev_personal`（`APIKEY` / `BASE_URL` / `MODEL`）。
模型 `deepseek-flash` @ `api.deepseek.com`。

**B2 评语轨**（`HttpCommentaryProvider.complete`，真实出网）：

```text
configured: True   model=deepseek-flash timeout=120.0s
OK in 2.0s
text: 末尾换行符也是字符。若标准答案没有它，精确逐字符比较就会多出一字符而不相等，导致判错。
```

**B3 出题辅助**（`HttpAuthoringProvider.advise`，真实出网，`RQ-T-10`）：

```text
enabled=True model=deepseek-flash timeout=120.0s
outbound payload keys: ['adapter_kind','case_count','case_ordinals','case_weights','comparison',
                        'language','max_files','max_total_bytes','notes','stem_markdown','title','topics']
OK in 15.0s -- 2 suggestion(s)
  [difficulty] ...3 个等权用例可能不覆盖 strip() 边界；题干未声明课程层次...
  [comparison_policy] ...exact + ignore_trailing_whitespace 对前导空白/加号/前导零仍会误判...
```

两条建议都**未**产生期望输出、**未**声称题目正确，符合 `ADR-0009` 决策 3。

### 3.4 事件链收口

启动 B3 的 `consumer_cli` 与 B4 的 `messaging relay` 前，三个发件箱的积压从未被投递过
（`attempts=0` 且 `last_error` 为空 = 中继从未运行，与"投递失败"是两种不同的故障）。
启动后：

```text
table                         unpubl    publ  attempts   dead    recv    proc
b2.outbox_events                   0      15         1      0      15     n/a
b3.event_outbox                    0     103         1    n/a     103     n/a    ← 之前 103/0/0
b4.outbox_events                   0       2         1    n/a       2     n/a

JetStream streams:
  DOMAIN_EVENTS: messages=118 bytes=74781
  ROSTER_IMPORT: messages=2 bytes=1064
  B4_DLQ:        messages=0 bytes=0
```

**三个发件箱全部清零、`attempts=1`（无重试膨胀）、死信 0**，正是 `phase-01 §12.3.1`
要求的读数。**`b3.event_inbox` 仍为 0 / 0**，与文档一致：它需要一次**真实完成的评测**，
而那需要 Linux 的 nsjail —— 不得因为发布端全绿就暗示消费端已验证。

### 3.5 配置权威：Doppler + 渲染器

`.env.integration` 此前是**手工维护且 gitignored**，于是"这个部署设了什么"没有可评审的答案。
现在分两层：

- **基础配置**（31 个键）存在 Doppler：DSN、端口、路径、JWT 设置、超时，以及 provider 的
  三个 bedrock 值（`APIKEY`/`BASE_URL`/`MODEL`）。
- **派生 provider 块**（8 个变量）由 `scripts/render_integration_env.ps1` **渲染**，不存储 ——
  存储它会把同一个 API key 放进三个地方（本项目已三次移除这个模式）。
  渲染规则里有一处必须写明的**不对称**：B2 取 **base** url（它自己追加 `/chat/completions`），
  B3 取**完整端点**（它逐字 POST）——配错会得到一个"存在但永远失败"的 provider。

**闭环实测**：备份 → 渲染 → 对比键集合。

```text
wrote .env.integration  (31 base + 8 rendered = 39 variables)
before: 38 vars   after: 39 vars
only in before: (none)
only in after:  B3_AUTHORING_TIMEOUT_SECONDS
starts with BOM: False
```

**再证"它不是唯一副本"**：把 `.env.integration` **删除**后从 Doppler 重建，与删除前**字节完全相同**：

```text
deleted: True
wrote .env.integration  (31 base + 8 rendered = 39 variables)
byte-identical after rebuild: True      ← SHA-256 相等
```

这是"配置权威在 Doppler、本地文件是派生产物"这句话的可执行判据，而不是一句声明。
在此之前，`.env.integration` 是**唯一**存放这些取值的地方，且 gitignored —— 换一台机器等于不知道
这个部署设了什么。

### 3.6 契约 0.13.0（`CHG-0018` / `ADR-0014`）`RQ-T-30`：`EvaluationCommentary.advisory_score`（顾问性评分参考），`AdvisoryScore` 四字段。

```text
sync_contract_version --to 0.13.0 : 9 处派生声明同步
relock_contracts                  : B1–B5 全部 0.13.0 / b2b8fcfff11f2c5ce4eb4c1b9da2feec87801aaa24d5ac872ef242cf56db006a
门禁（重装 B3/B4/B5 前）：check_requirement_baseline PASS、check_change_records PASS、
                        check_decision_references PASS、check_contract_parses_strictly PASS
```

**本轮同时修正的一处 MAIN 自身漂移**：`docs/requirements/traceability.md` 的 §3B 写着
"两项目前都没有契约与实现，只有 ADR 裁决"，而 `CHG-0011` 早在 0.6.0 就把两个端点写进了契约、
B2 与 B3 也都已实现。它是 MAIN 矩阵停留在 ADR-0009 裁决时刻的快照。已按实测改写，并把
`RQ-T-20` 如实标为 `待实现` —— **不是**因为 B2 没做，而是因为它的第二个责任方 B1
**从未消费 `/commentary`**（零调用点）。一个需求缺一半交付就是没交付。

### 3.7 SDK 变更的回归检查（B3/B4/B5 三个套件）

`deployment_root()` 加进 SDK 后，三个依赖它的模块各自在**自己的虚拟环境**里重装 SDK 后复跑：

| 模块 | `ruff check .` | 套件读数 |
|---|---|---|
| B4 | All checks passed! | **227 passed**, 2 warnings in 127.79s |
| B3 | All checks passed! | **364 passed**, 6 warnings in 43.89s |
| B5 | All checks passed! | **28 passed**, 2 warnings in 4.71s |

**无回归。** B5 首次运行时出现 5 个 `ERROR`，全部是
`PermissionError: [WinError 5] ... 'D:\\dev\\temp\\pytest-of-LEGION'` —— pytest 的 `tmp_path`
默认落在 `D:\\dev\\temp`，而 `D:\\dev` 正是 `phase-01` 记录过 `uv` 缓存权限问题的同一个位置
（在工作区之外）。加 `--basetemp=<工作区内目录>` 后 28 passed。

**这一条要写下来**：它看起来完全像一个由 SDK 改动引起的回归（5 个 error），实际是环境。
下一个人若不看这行记录，会去改代码。

另外 MAIN 侧的 `ruff check sdk host scripts` 报 **3 个既有错误**，全部在
`scripts/event_chain_status.py`（`F401` 未使用的 `sys`、两处 `B023` 闭包捕获循环变量）。
该文件不是本轮改动的，按"发现无关缺陷只报告不顺手改"处置，**未修改**。

### 3.8 真实提交的端到端（这一步发现了 §2.5）

脚本 `MAIN/.runtime/e2e_submission.py`：登录播种的学生 → 取会话 → 构造 `submission-envelope`
→ 提交 → 轮询状态机 → 读报告与评语。

```text
=== 1. login as the seeded student ===
  POST /auth/session -> 200
  student_id = fd29cd1d-9ec5-43f5-89a9-eca6f822e96d
  roles      = ['student']

=== 2. the contract-schema path (this is what the deployment-root fix unblocked) ===
  GET /questions -> 200  items=3

=== 3. submit ===
  POST /submissions -> 202
  {"submission_id":"2a5d589f-…","state":"validated","evaluation_run_id":null,
   "idempotent_replay":false,"accepted_at":"2026-09-20T09:13:09.012Z"}
```

**`202` 这一行就是 §2.1 修复的直接证据**：B2 的接收路径每次都要调
`contract_schema_errors(SUBMISSION_ENVELOPE_SCHEMA, payload)`，而修复前它在这里必抛
`ContractUnavailableError`（契约根解析到 `<venv>/MAIN/contracts`）。

worker 侧（`MAIN/scripts/run_module_process.ps1 -Module B2 -Entry autograder_b2.worker`）：

```text
worker.evaluating submission_id=2a5d589f-… question_version_id=53f8ac1a-…
evaluation.run_failed
worker.evaluated … evaluation_run_id=a7f97d4c-… state=failed
worker.outbox_drained
```

即**提交被受理、被 worker 取到、被评测**，然后终态 `failed` —— 失败原因不是沙箱不可用，
而是 §2.5 的取包 409。这正是"真跑一次端到端"的价值：它把一句
"评测链在本机是断的"（`phase-01` 的表述）变成了一个**具体的、可修的**原因。

**这一点必须如实说清**：在 §2.5 修好之前，本项目的**评测链在集成环境里从未成功过一次**。
103 个已发布版本没有一个能取到包。

### 3.9 B1 侧交付（`CHG-0014`/`0015`/`CHG-0018` 的界面）与 MAIN 的独立复核

B1 的工作包把五处"契约有、界面无"的能力接通：评语轨（挂在既有成绩下钻视图，无新路由）、
AI 参考分（与权威分同区域并列）、隔离区复核台（新路由 `/intake/quarantines`）、证据包下载、
题目质量热力图（新路由 `/quality`）。它的自报读数：

```text
eslint --max-warnings 0        -> 0
tsc -b --pretty false          -> 0
generate-api-types --check     -> API types match MAIN/contracts.
vitest run                     -> 30 files / 225 tests passed   （原 175，+50）
vite build                     -> dist/assets/index-Cte5rf7p.js 561.45 kB, built in 39.91s
```

它做了**唯一一处真机联调**（`/commentary`）：`GET` → 200/`ready`，真实 DeepSeek 评语 + 两条标注 +
出处与输入白名单；未生成 → 404 且 detail 明说"不等于运行不存在"；`POST` → 202（5882 ms）；
并用浏览器在 `/app` 上逐条核对。并在真机上发现并修掉两处契约推断不出来的缺陷：
**404 被当成读取出错**（现为信息态）、**`failed` 运行没有报告时下钻把评语轨一起吞掉**。

它同时如实标注了三处**仅对契约构建、未经真实 B2 验证**（`/intake/quarantines`、`/appeal-pack`、
`/quality-summary` —— B2 尚未实现，真机原样显示服务端 404），以及 `advisory_score` 非空路径
（运行中的 host 早于契约 0.13.0）与学生视角（课程级策略未打开）只有夹具证据。

**MAIN 的独立复核**（不采信自报，只采信自己能读到的）：

```text
B1/dist/assets/index-Cte5rf7p.js 存在于磁盘
GET /app/                        -> 200，且 HTML 引用的正是 index-Cte5rf7p.js   ← 与自报一致
GET /app/intake/quarantines      -> 200，返回应用外壳（SPA 深链回退生效）
GET /app/quality                 -> 200，同上
```

即"构建产物确实是宿主在服务的那一份"这一点被独立确认，而不是只由代理声明。

### 3.10 完整验收：门禁全绿 + 真实 HTTP 端到端（B2 与 B1 两个工作包落地后）

```text
MAIN 门禁                                  14/14 PASS
contracts, manifests and requirements: ok (03d2bb862feda62f233e57dd5cb5eb0d3f119aa4c91b06a09a7b1ed8f643e18a)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok

重建宿主后的路由面（probe_host_assembly.py）
  host assembled: 46 route paths        ← 重启前 42
  routes from CHG-0011..0015: 全部八条已注册，且**不再有 NOT REGISTERED 一行**
  restart-ready: every loaded module reports healthy

完整验收（.runtime/acceptance_full.py）：passed 12 / failed 0
  GET /intake/quarantines        -> 200   （重启前 404）
  GET /quality-summary           -> 200，只含计数，三态安全结论分开，unmeasured_runs 存在
  POST /submissions              -> 202 → 终态 failed
  grades 载荷不含 advisory_score
```

**最关键的一条是那个终态的"原因"**。数据库里的运行转换序列（新到旧）：

```text
failed  | 沙箱不可用：本次评测未能执行，这不是学生代码的问题。      ← 本次提交
running | 静态与安全检查通过，进入沙箱执行。
queued  | -
failed  | 评测包不可获取或不可解析：本次评测未能执行…             ← §2.5 修复之前
```

**评测已经越过取包阶段**（进入 `running` 的前提就是包已取得），停在沙箱 —— 这正是 `ADR-0004` 与
`SEC-20` 在 Windows 上的正确行为：**宁可如实报告"沙箱不可用"，也不降级为裸进程执行**。
`case_evidence = 0` 同样合理：没有用例被真正执行，就不该有逐用例证据。

至此 §2.5 的修复经**真实 HTTP 链路**独立确认（此前只有 B3 自身取包路径的 6/6）。
**仍未验证**：Linux 沙箱下的真实 `completed` + 分数路径（本环境不可能）。

### 3.11 平台一致性审计，以及我自己的探针又有一处"读数说得比事实多"

**B5 的一致性端点**（`GET /api/v1/platform/conformance`，教师令牌）：

```json
{"status": 200, "consistent": true, "loaded_modules": ["B4","B3","B2","B5"],
 "contract_path_count": 45, "differences": []}
```

**但要把这句话读准**：B5 比对的是**前缀声明**（每条契约路径是否落在某个已声明前缀下、每个已声明前缀
是否被用到），因此 `consistent: true` **不等于**"每个契约端点都有人实现"。我一度按后者期望它，
那是我读错了它的职责 —— 它自己在 `README` 与 `docs/testing/strategy.md` 里写明了这个边界
（"不替代 MAIN 静态门禁"、"不做方法级比对"）。

**发现"契约声明了但没人实现"是探针的职责，而我的探针当时做不到**：它打印的
`NOT REGISTERED` 只针对一个**硬编码的 marker 元组**（CHG-0011…0015 的七个片段）。
刚随 `CHG-0020` 声明的 `/api/v1/questions/coverage` 不在那个列表里，于是**契约里有、实现里没有，
而探针一声不响** —— 与它最初那个"7 route paths"的错误归因是同一类：**读数说得比事实多**。

**修法：删掉硬编码列表，改为与契约的全量双向比对**：

```text
contract vs composition (45 declared path(s)):
  declared but NOT served (1): ['/api/v1/questions/coverage']
  served but NOT declared (0): none
```

这正是当时的事实：`coverage` 已声明（契约 0.14.0）、B3 的实现正在进行中；而没有"服务了但未声明"
的路径。硬编码列表消失后，**下一个新端点不可能再被漏报**。

顺带记录一条被这次比对确认的事：`/health/*`、`/app`、`/docs`、`/redoc`、`/openapi.json` 是宿主
自有路由，**故意不在契约内**（ADR-0007），探针把它们显式列为"宿主所有"而不是当成差异。

### 3.12 环境复核：三个工作包在飞期间，集成栈仍活着（2026-09-20）

本轮的三个工作包（B2 `RQ-T-30`、B3 覆盖度端点、B1 覆盖度视图）**正在改源码**，因此安装副本与
工作树分叉是必然的。但在此之前先按 goal 的要求复核"工作区是否仍如我所信"，读数如下：

| 读数 | 值 |
|---|---|
| `GET /health/live` | `{"status":"ok"}` |
| `GET /health/ready` | `ready=true`；B4/B3/B2/B5 四模块 `healthy=true` |
| B4 detail | `migration head 20260915_0002 matches module.yaml`；`nats reachable at 127.0.0.1:54222` |
| B3 detail | `service_token_configured=True` |
| B2 detail | `evaluation-chain: ok`；`message-broker: ok（未发布 0 条，DLQ 0 条）` |
| B5 detail | `45 contract paths under …\MAIN\contracts` |
| `docker ps` | `main-postgres-1`/`main-nats-1`/`b2-*`/`b3-*`/`b4-*` 全部 `Up 2 hours`，postgres 均 healthy |

**两处必须写明的口径**：

1. **宿主机的健康端点是 `/health/live` 与 `/health/ready`，不是 `/health`。** 我最初按 `/health`
   调用拿到 404，那是**我的调用错误**，不是宿主的缺陷（`probe_host_assembly.py` 里的 `/health`
   是**前缀**匹配，恰好覆盖这两个）。记在这里以免下一个人重犯。
2. 同一次读取里 B2 的 detail 在 PowerShell 控制台显示为乱码（`è¯æµé¾å¯ç¨`）。**逐字节复核后
   判定为客户端现象**：用 `DownloadData` 取原始字节并按 UTF-8 解码得到
   `评测链可用（取包端点、工作区根与库结构版本均已就绪）`，且**不含 `U+FFFD`**。原因是
   PowerShell 5.1 对不带 `charset` 的 `application/json` 按 Latin-1 解码。**宿主发出的字节是合法 UTF-8。**

**同一读数还确认了一件事**：宿主机当前的路由表里**没有** `/api/v1/questions/coverage`
（`openapi.json` 实测 45 条路径），因为它跑的还是**旧安装的 B3**。这与 `check_module_installations`
的红完全吻合 —— **重装 + 重启宿主是覆盖度端点生效的必要步骤，不能跳过。**

> **为什么现在不重装**：三个工作包会跑它们自己的测试，而 `uv sync --reinstall-package` 会在它们
> 测试期间替换 `site-packages`，从而给它们制造 flaky 失败。**等它们结束后再统一重装**，这是
> 尊重其他写入者的必要约束，不是拖延。

### 3.13 新增门禁 `check_shared_vocabularies()`：把一处静默漂移面收口

**为什么会有这一条。** 准备 `CHG-0019` 时要把 `failure_category` 的 8 个取值写进 `b2.yaml`，
于是去查它现在的权威定义，发现权威在 `contracts/schemas/evaluation-report.schema.json:107`
（`timeout` / `crash` / `output_mismatch` / `resource_limit` / `missing_output` / `import_error` /
`security_blocked` / `infrastructure_error`）。

而 OpenAPI **无法** `$ref` 进 `schemas/*.json`——不同方言、不同文档。所以凡两边都要命名的词表，
**必然写两遍**。这不是新问题：`language` 早就写了两遍（`openapi/common.yaml:48` 与
`evaluation-package.schema.json:31`，都是 `[python, bash]`），而**门禁树里没有任何东西比对这两份副本**。

失败形态比"两边不一样"更难查：**每份文档单独看都自洽**，而按其中一份校验的消费者会拒绝另一份
允许的载荷。`CHG-0019` 会把这种重复**再开一处**，所以它那句"复用已有词表、不新造"如果只写在
文档里，就只是一句承诺。

**加了什么。** `checks.py` 新增：

- `SHARED_VOCABULARIES`：显式登记的 (OpenAPI 文件, 路径, schema 文件, 路径) 对，当前一对（`language`）；
- `check_shared_vocabularies()`：两侧非空成员必须**集合相等**，差异逐项报出；
- 已接入 `main()`，因此 `gate_status.py` 从 `main()` 源码读顺序时会自动带上它。

**一个刻意的设计选择（理由后来被我修正过一次）。** 可空性**单独**比对，两侧都先剥掉 `null` 再比集合。

我最初写的理由是"OpenAPI 3.0 不能把 `null` 放进 `enum`，所以要用 `nullable`/`anyOf`"——**这个理由是错的**：
本项目是 **OpenAPI 3.1**，它和 JSON Schema 2020-12 对齐，`enum` 里**可以**直接含 `null`。正确的理由是
**同一个可空性两种拼法都合法**：JSON Schema 可以把 `null` 列为 `enum` 成员，而 3.1 之前风格的写法用
`anyOf: [ ..., {type: "null"} ]`。逐字比对会把这种拼法差异报成"词表分歧"——而**会乱叫的门禁会被关掉**。
剥掉 `null` 后比集合，比的是**值集本身**；可空性由各自的写法表达，本门禁不裁决它。
（理由写错而行为恰好正确，正是需要记下来的那类事：若我按错误理由去"修正"实现，就会把它改坏。）

**这条门禁现在还覆盖不到 `failure_category`**（那一对尚不存在），所以 `CHG-0019` 的实施清单里
已写明：**重复该词表的同一次变更必须把这一对登记进 `SHARED_VOCABULARIES`**，否则本门禁只是
覆盖了 `language` 一处。这句话同时写在 `checks.py` 的常量 docstring 与
`docs/testing/strategy.md` §5 的表里，三处一致。

**变异证明（三步，缺一不可）。** 判据不是"门禁跑了通过"，而是"门禁在被破坏时确实失败，
且还原后字节复原"：

| 步 | 操作 | 读数 |
|---|---|---|
| 1 | 真实树 | `check_shared_vocabularies` **PASS**；摘要 `03d2bb862feda62f233e57dd5cb5eb0d3f119aa4c91b06a09a7b1ed8f643e18a` |
| 2 | 把 `common.yaml` 的 `Language.enum` 单侧改成 `[python, bash, rust]` | **FAIL**，消息逐项：`declared by OpenAPI but not by the schema: ['rust']` |
| 3 | 还原 | 门禁 **PASS**，摘要回到 `03d2bb86…`，**逐字节一致** |

第 3 步是必须的：只证明"会失败"而不证明"还原干净"，就没有排除"我在验证过程中污染了契约树"。
摘要与验证前**一字不差**，因此**五份 `contracts.lock` 无需改动**——这正是"摘要由工具计算、
绝不手填"能给我的保证。

**门禁总数 14 → 15，本轮实测 13/15。** 两条红仍是同一根因的两种表现（工作树与安装副本分叉、
在制品死链），且**都不在本门禁的判据内**。`ruff check host/src/autograder_host/checks.py` 通过。

**顺带更正两处过期记录**：`docs/testing/strategy.md` §5 原写"OpenAPI/AsyncAPI 的结构解析校验
（目前只做 YAML 载入）"，但重复映射键的严格拒绝**早已由 `check_contract_parses_strictly()`
实现**；该行已改为准确表述（仍未实现的是**元 schema** 校验）。该表同时补入了本门禁。

### 3.14 `401` 与 `422` 的先后：B2/B4 与 B3 **相反**，实测确认后裁决为"不受保证"

**触发**：B3 实施 `CHG-0020` 时按规程停下上报——无令牌**且**缺必填 `course_id` 得到 `422` 而不是
`401`，并请 MAIN 裁决是否应全面改为"鉴权优先"。我没有采信转述，用真实端口（`127.0.0.1:18000`）
自己复测了 15 个请求，读数如下（均为**无令牌 + 请求不合法**）：

| 模块 | 请求 | 读数 |
|---|---|---|
| B2 | `GET /submissions/not-a-uuid` | `401` |
| B2 | `GET /evaluation-runs/not-a-uuid` | `401` |
| B2 | `GET /evaluation-runs/quality-summary?question_version_id=abc` | `401` |
| B3 | `GET /questions/not-a-uuid` | `422` |
| B3 | `GET /question-versions/not-a-uuid` | `422` |
| B3 | `POST /question-versions/{x}/publish` | `422` |
| B3 | `POST /questions`（空体） | `422` |
| B3 | `GET /questions/coverage`（缺 `course_id`） | `422` ⚠️ **见下** |
| B4 | `POST /courses`（空体） | `401` |
| B4 | `GET /users/not-a-uuid`（该路径无 GET） | `405` |

**差点被我误读的一行（必须记下来）**：`GET /questions/coverage` → `422` 一度被我读成"新端点已经
在跑"。**不是。** 宿主机跑的还是**旧安装的 B3**（§3.12 已证 `openapi.json` 里没有这条路径），
所以 `coverage` 被 `/api/v1/questions/{question_id}` **当成路径参数吞掉了**，`question_id="coverage"`
不是合法 UUID → 由 FastAPI 校验产出 `422`。若路由表里没有兄弟参数路由，这个请求应是 `404`。
**这恰好是 `CHG-0020` 契约描述里写的那个陷阱的活证据**——不是新端点生效，而是旧路由吞并。
把它写在这里，是因为"读数正确但归因错误"比读错数字更危险，而它离一个错误结论只差一步。

**根因：两处都自洽的刻意选择，不是疏漏。**

- B2 把路径/查询参数声明为 `str` 并在服务层自行解析。`evaluation_routes.py:100-108` 的 docstring
  写明了理由：**"`question_version_id` 仍按形参声明，但**不加** FastAPI 的类型校验：非法取值的
  422 由本模块统一产出（`Problem Details`）"**。因此读令牌的函数体在参数校验**之前**运行 → `401` 先。
- B3 用 FastAPI 的声明式校验（`question_id: uuid.UUID`），再由 `api/app.py:33` 的
  `RequestValidationError` 处理器产出 Problem Details → 校验先于函数体 → `422` 先。
- **两者产出的错误体形状一致**，差异只在"校验发生在哪一层"。

**裁决：不强行统一，把"平局顺序不受保证"写进契约正文**（`CHG-0021` §2.6）。理由是收敛要付出一处
**真实的设计回退**，而不是"改起来麻烦"：让 B2 改用 FastAPI 声明式校验，会使 422 由**两个**产出者
生成，丢掉"错误体形状由一处保证"这一性质；让 B3 把令牌读取提到路由级依赖要改约 17 条路由，而换来
的差别对客户端没有可观察后果（B1 对 `401` 走刷新重试、对 `422` 走字段错误提示，**两条路都正确**）。

同时写明**两条确实稳定**的事实，供消费者依赖：**① 路由存在性与方法匹配（`404`/`405`）先于两者**
（实测 `GET /users/{id}` → `405`；这不新增泄露面，因为哪些方法在哪些路径本就写在公开契约里）；
**② `403`/`404`/`409` 永远不与 `401` 竞争**，它们是**已认证**请求的结论。

> **这一条与 §6.2 第 1 项、`CHG-0017`、`CHG-0021` §2.5 同族（契约沉默被当成契约完备），
> 但处置方式相反，且这个区别必须说清**：§2.5 的沉默**必须被填上**（省略语义有唯一正确解——
> `boolean` 表达不了"两者都要"）；而这里的沉默**只能被宣布**（强行定序会损失一处真实性质）。
> 把两者混为一谈，会让人以为"所有沉默都该填一个值"。

**顺带澄清 B3 上报的一条并发写入观察**：B3 注意到 `MAIN/contracts/openapi/common.yaml` 在本轮之内
（18:00:02）被写过。**那是我的变异测试**（§3.13 第 2 步：把 `Language.enum` 单侧改成
`[python, bash, rust]` 以证明新门禁确实会拦），第 3 步已还原，且摘要回到
`03d2bb86…` **逐字节一致**。它**不是**一次未记录的契约变更，五份锁无需改动。

### 3.15 覆盖度在真实宿主上验证通过；并由此撞出一个"已生效但零实现"的 CHG

#### A. 覆盖度端点：真实宿主 + 真实 JWT + 真实题库 → 通过

B3 结束且不再写 MAIN venv，因此可以安全重装它（B2/B1 不 import B3，且宿主进程已加载的模块不受磁盘替换影响）。

1. `uv sync --all-groups --extra modules --reinstall-package autograder-b3` → B3 安装副本清干净。
   **这里我先犯了一个错，必须记下来**：第一次跑的是
   `uv sync --all-groups --reinstall-package autograder-b3`，**漏了 `--extra modules`**。模块是通过该
   extra 安装的，于是 uv 判定这四个包"不再被需要"并**全部卸载**——门禁从 `13/15` 掉到 `11/15`，
   新增两条红（`check_module_descriptors`、`check_contract_digest_implementations`），报错全是
   `No module named 'autograder_b3'`。用正确形式恢复后回到 `13/15`。
   **两个陷阱彼此独立，必须同时避开**：漏 `--extra modules` → 卸载；漏 `--reinstall-package` →
   uv 复用缓存 wheel 而不重建（B2 的报错会照旧，B3 也仍被报"陈旧"——我实测过一次，以为重装成功了其实没有）。
2. `probe_host_assembly.py` 读数：
   `host assembled: 47 route paths`；`declared but NOT served (0): none`；
   `served but NOT declared (0): none`；`restart-ready: every loaded module reports healthy`。
   **对比本轮之前**：那时是 `declared but NOT served (1): ['/api/v1/questions/coverage']`。
   `47 = 45 条契约路径 + /health/live + /health/ready`（`/app` 是静态挂载，§3.11 已说明）。
3. 重启宿主（`job_kill pwsh-12` → `run_host.ps1 -Port 18000`）→ `/health/ready` 的 `ready=true`，
   四模块 `healthy=true`；`GET /api/v1/questions/coverage?course_id=…`（教师 JWT）→ **`200`**：

   ```json
   {"course_id":"8cf8259c-…","total_questions":102,"tagged_questions":0,"untagged_questions":102,"tags":[]}
   ```

4. `MAIN/.runtime/crosscheck_coverage.py`：用**独立 raw SQL** 算出真值再与端点比对 → **8/8 ALL AGREE**
   （total / tagged / untagged 三项各自相等、`tagged+untagged==total`、逐标签值与**契约顺序**、
   无零计数标签、响应不含任何 id）。

   **为什么第 4 步不能省**：`200` 只证明路由存在；一个"与自己一致"的计数什么也证明不了。
   交叉核对走的是完全不同的路径（raw SQL，不经 B3 的 repository 代码），这才是"读数是关于数据的，
   而不是关于算术的"。

**但这一条还不够强，必须写明**：真实题库里 **`tagged_questions=0` 且 `tags=[]`**，所以逐标签聚合
**两侧都是空数组**——`jsonb_array_elements_text` + `GROUP BY` 这条路径**没有被真正执行**，
"一题多标签 ⇒ `sum(per-tag) > tagged`"这个重叠性质更没被覆盖。要真正验证它必须先把标签写进去，
而**写不进去**。这就是 B 部分。

#### B. 缺陷：`CHG-0017` 契约已生效，实现从未落地

我尝试用真实 API 给题打标签（`PATCH /api/v1/questions/{id}` 只带 `tags`），得到：

```text
PATCH {"tags": ["unix"]} -> 422
detail: "request body failed validation: body.adapter, body.cases, body.language,
         body.stem_markdown, body.title"
```

根因在 `B3/src/autograder_b3/dto/internal.py:136`：

```python
cases: tuple[TestCaseInputModel, ...] = Field(min_length=1)   # 无默认值 ⇒ 必填
```

而契约 `contracts/openapi/b3.yaml:922` 是 `required: [title, stem_markdown, language, adapter]`
——**不含 `cases`**，且 `:907-921` 的正文明写
"**Omission preserves (CHG-0017)** …`cases` omitted -- the version keeps the cases it already has"。

`CHG-0017` 的状态是 **已生效（2026-09-19 判定）**，其 §4 第 63 行给 B3 的待办原文是：
**"`cases` 改为可选（去掉必填），并且 `replace_cases` 只在字段被提供时调用……
判定'是否提供'要读 `model_fields_set`"**；§5 还写明"实现落地后才可执行，未完成前不得声称已通过"。

**`grep` 全 B3（源码 / 测试 / 文档）对 `CHG-0017` 零命中** —— 所以这不是"改错了"，是**从未实现**。

**为什么这不是小事**：契约那句描述自己的理由是"This is what lets a caller **rename a question or
correct its tags without reading and re-submitting hidden test cases and the reference answer**"。
现在做不到：任何编辑都必须把受保护内容读出来再发回去——正是"执行者 ≠ 记录者"要避免的那次往返。
而且 **B1 依 `CHG-0017` §4 第 64 行的指示按住了出题表单**（"在 B3 落地之前不得放开"），
所以**打标签这条写路径在真实系统里完全走不通**。

#### C. 调度结论

覆盖度的写路径（打标签）是 B1 出题表单的前置，而它被 `CHG-0017` 的缺失实现挡住。链条是：
**B3 实现 `CHG-0017` → B1 才能放开"只改标签"的保存 → 才能写出非空标签 → 才能在真实数据上验证
逐标签聚合与重叠性质**。已就此给 B3 派工作包（不需要契约变更，也不需要迁移）。

#### D. 方法论教训（值得单独记）

这个缺陷躺在一个**已生效**的 CHG 里。我读过那份 CHG 的**索引行**（"已生效，0.11.0 → 0.12.0"），
却没读它的 **§4 待办表**；`check_change_records()` 只断言"文件有状态行、被索引引用、版本跃迁出现在
CHANGELOG 里"，**它无法知道实现是否落地**。直到我在真实 API 上做一件真实的事（给题打标签）才撞到。

> **"契约已生效"不等于"实现已落地"。** 唯一可靠的判据是到真实系统上执行那条工作流。
> 这与 `phase-01` 反复记录的同一类问题一致——**门禁能证明的东西比人们以为的窄**。
> 下一步应把这条变成可执行的：`CHG-0017` 的验证项必须在实现落地后真跑一次，而不是留在文档里。

### 3.16 契约 `0.14.0 → 0.15.0`：CHG-0019 + CHG-0021 + CHG-0022 一批落地

B2 落地后 `CHG-0019` 的阻塞解除，三份 CHG 同批升版（都要动同一组文件，分开升只制造多次描述符同步）。

**变更与工具读数**

| 步骤 | 命令 | 读数 |
|---|---|---|
| 版本 | 六份契约文件的 `info.version` | `0.14.0 → 0.15.0` |
| 摘要 | `scripts/relock_contracts.py` | `03d2bb862fed… → 9ae4ebfef66327b386756565d2a7709c4562cd1ee8778ace9df4f53ef19a2921`；五份锁同步为 `0.15.0` |
| 描述符 | `scripts/sync_contract_version.py` | 9 处声明：5 份 `module.yaml` + 4 个 `plugin.py` |
| 复核 | `relock_contracts.py --check` | 五份全部 `up to date (0.15.0, 9ae4ebfef663...)` |

**一处只有真跑才会发现的坑（值得记）**：`declared_contract_version()` 扫描的契约文件**不止五份 OpenAPI**——
它还包括 **`contracts/asyncapi/events.yaml`**。我只改了五份 OpenAPI，`relock` 立刻报
`contract files disagree on info.version: {'events.yaml': '0.14.0', ...}`。**门禁替我抓住了遗漏，
而不是让我把一份不一致的契约交出去**——这正是"摘要由工具计算、绝不手填"的价值：手填摘要时我会把
`events.yaml` 忘掉而没有任何东西会告诉我。

**新增门禁覆盖**：`check_shared_vocabularies()` 的登记从 1 对增到 **3 对**
（`language`、`failure_category`、`adapter_kind`），因此 `CHG-0019` 那句"复用已有词表、不新造"现在由工具保证。
其中 `adapter_kind` 是 **OpenAPI↔OpenAPI** 的重复（`b2.yaml` ↔ `b3.yaml`）——不能跨引，因为 B2 的契约测试只从
`common.yaml` 解析外部引用；为此门禁的 `SHARED_VOCABULARIES` 改成按后缀加载并支持列表下标。
另外 `failure_category` 的**首个路径猜错了**（写 `QuestionVersionSummary`，实际是 `QuestionVersionPublic`），
门禁报 `no value at components.schemas.QuestionVersionSummary.properties.adapter_kind.enum` 而不是静默通过——
**一个会在路径写错时报错的检查，比一个"找不到就跳过"的检查可靠得多**。

**我自己的两处错误，都在本轮更正**

1. **CHANGELOG 里的 GIN 断言**：我只改了 `CHG-0020` §5，却漏了 `contracts/CHANGELOG.md:400` 同一句话的副本。
   两份文件对同一事实的表述必须一致，否则被推翻的断言会从另一处继续传播。已一并更正。
2. **我把理由写错了（而行为恰好正确）**：我在 `check_shared_vocabularies()` 的 docstring 与三份文档里写
   "OpenAPI 3.0 不能把 `null` 放进 `enum`，所以要用 `anyOf`"。**本项目是 OpenAPI 3.1**（`openapi: 3.1.0`），
   它与 JSON Schema 2020-12 对齐，`enum` **可以**直接含 `null`。正确的理由是"同一个可空性两种拼法都合法"，
   因此剥掉 `null` 再比集合是为了**容忍拼法差异**，不是为了绕开方言限制。已改掉 docstring 与三处文档，
   并在文档里写明"理由写错而行为恰好正确"这件事本身：**若我按错误理由去"修正"实现，就会把它改坏**。

**这次升版造成的预期红（契约先行的可见代价，不是回归）**

| 门禁 | 报错 | 谁负责修 |
|---|---|---|
| `check_route_declarations` | `contract path /api/v1/reference-verifications is under no module route prefix` | **B2**：`B2/module.yaml:7` 的 `routes` 加一项（该文件属 B2） |
| `probe_host_assembly.py` | `declared but NOT served (1): ['/api/v1/reference-verifications']`；`served but NOT declared (0)` | **B2**：实现端点 |
| B2 自己的 `test_registered_paths_match_the_contract` | `declared - served != _PENDING_CONTRACT_PATHS`（空集） | **B2**：实现端点后自然恢复；`CHG-0019` §2.5 明确**不**采用"把新路径登记进待实现集合"这种掩盖 |

> **这三条红是同一件事的三个独立读数，而且都是应当出现的。** 契约先行意味着"声明先于实现"，
> 而门禁的作用就是把这段差集**显示出来**而不是让它悄悄存在。`CHG-0019` §2.5 已经写明：
> 声明与实现必须在同一次变更里落地，**不得**用待实现清单把它盖住。

**我还自己制造过一次红并修好了**：`sync_contract_version.py` 改的是**源码** `plugin.py`，而**已安装副本**
仍是 `0.14.0`，于是 `check_module_descriptors` 报四模块 `descriptor.contract_version='0.14.0'` 与
`module.yaml:0.15.0` 不一致（门禁从 13/15 掉到 11/15）。处置是**重装四个模块**
（`uv sync --all-groups --extra modules --reinstall-package autograder-b2 --reinstall-package autograder-b3
--reinstall-package autograder-b4 --reinstall-package autograder-b5`），门禁回到 13/15。
**这不是"重装一下就好"的小事**：它说明"契约版本"在系统里有**第六份副本**（安装后的 `plugin.py`），
而 `sync_contract_version.py` 只覆盖源码。**升版之后必须重装模块，否则宿主下次启动会因描述符与清单不符
而拒绝装配。** 已写进 `CHG-0019` 的 B2 工作包说明。

**当前状态**：门禁 **13/15**；红的两条是 `check_route_declarations`（B2 待声明路由）与
`check_document_encoding_and_links`（B2 在制品 `phase-06` 里一处链接深度错误）。
探针 `restart-ready: every loaded module reports healthy`，B5 报 **46** 条契约路径（原 45，新增那一条）。
已派两个工作包：B2 实现端点 + 声明路由；B1 再生成类型并做出题页"验证并发布"入口（含 `CHG-0021`/`CHG-0022`
的两处适配）。

### 3.17 我的验收判据被实施侧纠正；`CHG-0017` 落地；三处新发现

#### A. `CHG-0017` 落地（373 → 382 passed）

B3 实现：`cases: tuple[...] = Field(default=(), min_length=1)`（省略 ⇒ `()`；`[]` 违 `minItems`；`null` 违类型），
写路径读 `model_fields_set` 判定"是否提供"，`replace_cases` **只在被提供时**调用；分支版本按 `CHG-0017`
原文把上一版内容**重新签发**成新版本自己的行。

**真实栈读数（B3 在 18001 上临时起的真 uvicorn，非 composition host）**：
`PATCH …?as_new_version=true`（body **不含** `cases`/`reference_answer`）→ `200`；
`revision_diff.reference_answer_digest_before == after == ccca4a3d0e01…`、`case_count 4 == 4`、
`case_set_changed=false`；随后覆盖度 `tagged_questions` **0 → 1**、`tags=[{tag: unix, question_count: 1}]`。
**关键读数**：调用方一次都没发送过受保护内容，而参考答案与隐藏用例原样继承。

#### B. **我的验收判据是错的，实施侧纠正得对**

我在工作包里要求"`PATCH {"tags":["unix"]}` 应为 **200**"。**这在 `CHG-0017` 范围内不可能成立**：
契约的 `QuestionDraftInput.required` **一字未改**，仍是 `[title, stem_markdown, language, adapter]`
——该 CHG 只让两个**受保护**字段可省略。我观察到的 422 里 `body.cases` 只是五个缺失字段之一。

B3 按 `CHG-0017` §3 的原文实现，并把这个边界**钉成测试**（`{"tags":[…]}` → `422`，detail 列出四个公开必填字段
且**不再出现** `body.cases`），且**没有**顺手给那四个字段也加省略语义——那需要另一个 CHG
（`language`/`adapter` 还参与内容摘要）。**这是正确处置。**

`CHG-0017` §5 第 1 项的原文本身也不够精确（"省略 `cases` 的 PATCH 返回 200"没写明其余四个仍必填），
已连同我的错误一起在该 CHG 里更正留痕；并已把这条边界**单独告知 B1**，因为它在做出题表单。

> **这是本会话我第四次因为"写下未核实的断言"而需要更正**（前三次：`CHG-0020` §5 的 GIN 索引、
> `CHANGELOG` 里同一句的副本、`check_shared_vocabularies` 的"OpenAPI 3.0"理由）。
> 四次的形状完全一致：**我把一个听起来合理的推断写成了读数**。唯一可靠的防线是让实施侧真的去执行那条路径
> ——本轮两次都是被实施侧撞出来的，不是被我的自查撞出来的。

#### C. B3 一次正确的判断，以及一处**瞬时的**宿主风险

B3 报告它**没能重启 18000 宿主**，因为当时启动新的 composition host 会失败：
`RegistryError: B2: module.yaml and the runtime descriptor disagree -- descriptor.contract_version='0.14.0'
but module.yaml declares '0.15.0'`。它的处置是**不动 18000**（动了会让它下线且起不来）、
改为临时起一个 18001 的真 uvicorn 做复测、结束即关闭，并如实说明那份读数**不是 composition host**。
**这个判断是对的**：它没有把一个"能起但我不确定"的服务当成证据。

**根因是 `§3.16` 已记录的那件事**：`sync_contract_version.py` 只改源码 `plugin.py`，安装副本是契约版本的
**第六份副本**。B3 撞到的窗口正是"`sync` 已跑、四模块重装未跑"之间。**现已修复并复核**：
`check_module_descriptors` PASS、`check_module_installations` PASS、探针 `restart-ready`、
18000 `ready=true` 四模块 `healthy=true`。**结论：契约升版后必须重装模块，这个顺序不是可选的。**

#### D. 三处新发现（已登记，未擅自处置）

| # | 发现 | 影响 | 处置 |
|---|---|---|---|
| 1 | **`adapter` 写必填却没有任何读出口**：`QuestionVersionPublic` 只暴露 `adapter_kind`/`max_files`/`max_total_bytes`，而 `args`/`stdin`/`expect_exit_code`/`comparison`/`files`/`callable` 读不回来 | **一个不自己保存 manifest 的纯 API 客户端改不了标签**——即使 `CHG-0017` 已落地。B1 能工作是因为它自己提交过 manifest。B3 复测时只能从 `b3.question_versions.adapter_manifest` 直读才凑出合法请求体 | 记入 `B3/docs/api/contract.md` 待决。**需要一个新出口**（若加进 `QuestionVersionPublic` 要留意 `adapter` 里可能含受保护信息），属契约变更，**本轮不动** |
| 2 | **分支版本不继承 fixture**：`fixtures` 不是 `QuestionDraftInput` 的字段（由单独端点上传），所以 `as_new_version=true` 的新版本声明了 `adapter.files` 却没有存储的 fixture 字节 | `publish` 会在摘要比对处拒绝它；`revision_diff.fixtures_changed` 如实报 `true`。**"先分支再发布"的流程会踩到** | 超出 `CHG-0017` 范围，B3 未改（正确）。**独立评估**，已记入 `B3/docs/phases/active/phase-08` |
| 3 | **集成库副作用**：题目 `81e746e7-4e5d-446d-bdb3-98cacbe6448e` 现在带标签 `unix`，并多了草稿版本 `a9a62fb8-00c5-4989-b7a6-a2ed3570fbe1`(v2) | 它原来的已发布版本 `01c50a65-…` 未被触碰、题目仍是 `published`；但该草稿**没有 HTTP 删除入口**，会留在库里 | 接受并记录。**它是 `CHG-0017` 验证留下的第一条真实标签，对覆盖度的非空演示有用**（`tagged=1`），因此不再清理 |

> 第 1 条与第 3 条合起来说明一件事：**"能用"与"能被 API 驱动"是两回事**。
> B1 的表单能工作，掩盖了"任何不自己缓存 manifest 的客户端都做不到"。
> 这类缺口不会被前端测试发现，因为前端正好不是那种客户端。

### 3.18 两项此前未执行的验证在真实 composition host 上完成（**ALL PASS**）

已安装的 B3 已含 `CHG-0017` 修复（实测安装副本 `dto/internal.py:153`
`cases: tuple[...] = Field(default=(), min_length=1)`），因此重启宿主即可在真实端口上复测——
这同时补上了 B3 因"新宿主当时起不来"而只能改在 18001 临时 uvicorn 上做的那一半。

脚本：`MAIN/.runtime/verify_chg0017_and_coverage_overlap.py`（真实 HTTP + 独立 SQL 交叉核对）。

**一、`CHG-0017` 在 composition host 上**

```text
PATCH /api/v1/questions/{id}?as_new_version=true
body keys = ['adapter','language','stem_markdown','tags','title']   ← 无 cases / 无 reference_answer
-> 200 ;  新版本 108787ac-9064-4e8b-a794-f00d6b929d7d -> 67231457-f276-46ac-a85d-978ddd7f951b
tags = ['virtual-memory','file-systems']
revision_diff: case_count 2 == 2 ; case_set_changed=false ; weights_changed=false
               stem_length 542 == 542 ; adapter_kind_changed=false ; size_limits_changed=false
               reference_answer_digest_before == after == 57024498afac3cbbf21e552cd56970d001eee1820864d213bd4af0eaf1555793
独立 SQL（受保护表直读，按 ordinal 逐字段摘要）:
  OLD 108787ac: cases=2:f10462d6c0e75d4e4a45871bf36a7a4d  answer=d162f95987d6…
  NEW 67231457: cases=2:f10462d6c0e75d4e4a45871bf36a7a4d  answer=d162f95987d6…   ← 逐字相同
```

最强的一条是最后一行：**新版本把上一版的受保护内容重新签发成了自己的行，而调用方一次都没发送过它。**
这正是 `CHG-0017` 存在的理由（"rename a question or correct its tags without reading and
re-submitting hidden test cases and the reference answer"）。

三次尝试的记录也值得留：**第一次我写的 PATCH 没带 `?as_new_version=true`，得到 `409`**
"this question's latest version is published and immutable"。那是**正确**的拒绝，也让这次验证顺带走过
"分支版本继承受保护内容"这条更关键的路径——而不是就地编辑那条更简单的路径。

**二、覆盖度的重叠性质（此前从未被执行）**

`CHG-0020` 保证的恒等式是 `tagged + untagged == total`，**明确不保证** `sum(per-tag) == tagged`
——一道题最多带 20 个标签。在此之前题库**一个标签都没有**，所以逐标签聚合与空数组**平凡地**相等，
重叠从未被触发。给一道题打**两个**标签即可让它成为真命题：

```text
endpoint : total=102 tagged=4 untagged=98
           tags=[('file-systems',3), ('virtual-memory',3), ('unix',1)]   sum(per-tag)=7
database : total=102 tagged=4 untagged=98   tags=[('file-systems',3),('virtual-memory',3),('unix',1)]
恒等式成立；逐标签值与**契约顺序**（count 降序、tag 升序）逐项相等；sum(per-tag)=7 > tagged=4
```

**三、顺带在真实栈上确认了 §3.17 D-2**

同一个 `revision_diff` 里 **`fixtures_changed: true`**——而这次 PATCH **什么都没改**（`stem_length` 未变、
`case_count` 未变、参考答案摘要未变）。这是"分支版本不继承 fixture"在真实宿主上的第一次直接观测，
从"B3 的判断"变成了"读数"。

**四、这次留下的数据副作用（已记录）**

我的三次验证各给一道题打了两个标签，因此库中现在的标签分布是
`virtual-memory=3, file-systems=3, unix=1`、`tagged=4`，并新增 3 个草稿版本
（`34059e08…`、`67231457…`、以及上一轮的 `a9a62fb8…`）。**这不是污染，而是让"题库覆盖度"从空态变为可演示**：
它现在有 3 个标签、逐标签计数不相等、且有重叠——正是这个视图要展示的东西。所有被改的题目仍是 `published`，
原已发布版本一行未动。

**方法论**：这两项之所以卡了这么久，都因为前置条件（`CHG-0017` 落地、非空标签）而无法构造。
**一旦前置条件满足就应当立刻补跑**——否则它们会永远停在"未执行"里，而文档读起来仍像是功能已被验证。

### 3.19 **最严重的一次错误：我记了一条没做过的变更**；以及"用契约校验真实响应"抓到的第四处缺陷

#### A. `CHG-0021` 的四处文本"记为已落地、实际没写"

**B1 发现的。** 它实现隔离区界面时去 `b2.yaml` 里找 `CHG-0021` 的文本，**一处都没找到**，按规程上报，
没有自行改 MAIN 的文件。核实后确认它完全正确——`0.15.0` 被记为已生效时，四处文本全是旧值：

| 位置 | 实际内容 | 本 CHG 要求 |
|---|---|---|
| `resolved` 描述 | `"Filter to unresolved (the default view a teacher wants) or resolved."` | 写明"省略 = 不过滤" |
| `resolveIntakeQuarantine` 正文 | `"The manual path re-runs the same validation as the automatic one."` | 可归属原因码集合 + 两处如实承认 |
| `reason_code` 描述 | `"Machine-readable cause, e.g. …"`（开放集合） | 19 项闭合词表 + 可归属标记 |
| `detail` | `maxLength: 500` | `1000` |

而 `CHANGELOG` 的 0.15.0 段把这四处**列进了"变更文件"**。**变更记录声称了一处不存在的变更。**

**根因**：那一批同时有 `CHG-0019/0021/0022/0023`。我在同一批里改了 `common.yaml`（§2.6）、
`b3.yaml`（`CHG-0022`/`0023`）、`b2.yaml`（`CHG-0019` 的端点），**却从未回头把 `CHG-0021` §2.1–§2.5
写进 `b2.yaml`**。而"变更文件"清单是照**该 CHG 的计划**写的，不是照**我实际改了哪些文件**写的。

> **这与前四次"把推断写成读数"是同一根因的更深一层。** 前四次是断言关于**系统**的事而没核实；
> 这一次是断言关于**我自己的工作**的事而没核实——而一份声称"已改 X"的记录，比一句关于系统的
> 未核实断言更有害，因为它会让下一个消费者**去契约里找一处不存在的文本**，然后要么以为契约错了，
> 要么以为实现错了。**B1 两者都没做，它上报了——这是唯一正确的处置。**

**处置**：补齐四处文本；重算锁（摘要由工具计算，`29271ee2480c…`）；版本仍 `0.15.0`（尚未被任何模块
实现消费）。**预防措施**：一批变更落地后，对**每一条被声称的文本变更**取一个独特字符串去 `grep`
目标文件，六项全存在才算落地——本次实测六项全 PRESENT。清单必须由**核对**生成，不能由 **CHG 的计划**生成。

#### B. `CHG-0022` 的验证：**反向断言全部通过**

用契约自己的校验器（`jsonschema` + 把 `$ref` 内联）校验**线上真实响应**：

```text
线上 QuestionTagCoverage 通过校验            tags=[('file-systems',3),('virtual-memory',3),('unix',1)]
反向（这才是证明约束存在的那一半）:
  "Process Scheduling"  → 拒  does not match '^[a-z0-9][a-z0-9-]*$'
  "Unix"                → 拒  does not match '^[a-z0-9][a-z0-9-]*$'
  "a"*41                → 拒  is too long
  "text_tools"          → 拒  does not match '^[a-z0-9][a-z0-9-]*$'
  question_count = 0    → 拒  0 is less than the minimum of 1     ← GROUP BY 结构上产不出它
线性 QuestionPublic 通过校验；21 个标签 → 拒（maxItems: 20）
```

**只证明"合法值通过"等于没证明约束存在**——`{type: string}` 也会让它通过。反向断言是这一条的分量所在。

#### C. 同一个校验器抓到**第四处**"契约写不出实现合法产出的值" → `CHG-0024`

校验线上 `QuestionPublic` 时炸在一处**与标签无关**的地方：

```text
QuestionPublic.versions[0].published_at: None is not of type 'string'
```

`QuestionVersionSummary.published_at` 与 `QuestionVersionPublic.published_at` **都**声明为非空 `Timestamp`，
而**草稿版本本来就没有发布时间**。实测库里 **7 个草稿全部 `NULL`**、103 个已发布全部非空。
后果：**任何带草稿的题目都无法被严格消费者读取**，而带草稿是出题流程的常态。
已写 `CHG-0024` 并放宽可空性（两处声明），重算锁。

**这是本项目第四次同一形状**（`CHG-0016`、`CHG-0021` §2.4、`CHG-0022`、本条）：
**契约照着"理想中的形状"写，而不是照着"系统实际会产出什么"写。**

> **而这一次的发现方式与前三次不同**：前三次靠**读代码/读表**，这一次靠**用契约校验一条真实响应**。
> 那一步本该更早做——它成本极低，且**抓到了三轮静态审查都没抓到的东西**。
> 建议把"用契约校验真实响应"变成常规动作，而不是只在某个 CHG 的验证项里做一次。

#### D. B1 的中间报告：`adapter` 缺口**不是"只影响脚本"**

我此前把它记为"不自己缓存 manifest 的客户端做不到"。B1 逐处核实后给出更强的结论：
**它自己就是重新构造的**——读出口只有 `adapter_kind`/`max_files`/`max_total_bytes`，
而 `adapter` 是 `QuestionDraftInput.required` 之一。所以打开一道在 UI 之外创建的题目时，那些输入框是空的，
保存会用**一份不完整的 manifest 覆盖已存的那份**。

**"能用"与"正确"是两回事**：B1 在同会话内（刚填过并保存的题）确实保留了用户输入，
但**跨会话/非 UI 创建的题目**上它会静默降级。B1 的处置是正确的：加了一条常驻横幅明说这一点，
并**没有**自造 carry-over 缓存（那会变成浏览器里的一份服务端状态副本），而是把是否缓存交给我决定。

**裁决：不自造缓存。** 正确的位置是**新增一个读出口**（契约变更），而不是让每个客户端各自维护一份
服务端状态的影子副本——那会立刻产生"谁的副本才是对的"这类问题，且每个新客户端都要再实现一遍。
该读出口需要设计（`adapter.files[].content_sha256` 与 `mount: expected_output` 对**学生**可能构成弱泄漏，
所以不能简单地把它加进 `QuestionVersionPublic`）。**记为待决，本轮不动。**

B1 另外两条值得记：① 分支不继承 fixture 这条限制**对 B1 的界面不可达**（`kind=file` 在 B1 本来就不可保存，
它在保存那步就被拦住），B1 如实写"无法触达该路径，因此没有界面读数"，**没有假装观察过**；
② 它删除了被取代的 `PublishRequirement.tsx` 与其 i18n 词条（**不留兼容层**），并删掉了覆盖度里那段
已不可达的防御分支，把取舍理由写进代码注释与文档。

### 3.20 `adapter` 读出口的裁决（`CHG-0025`），以及一条新的版本纪律

#### A. 裁决：新增"写形状的读形状"，而不是让客户端缓存

`QuestionDraftInput.required` 含 `adapter`，而 `adapter` 的六个字段
（`args`/`stdin`/`expect_exit_code`/`comparison`/`files`/`callable`）**一处都读不回来**
（`AdapterManifest` 里只有 `kind` 必填，而 `kind` 读得到）。
**缺口的确切形状是：必须重发的东西，恰好是读不回来的东西。**

**后果是数据损坏，不是"脚本不方便"**：B1 **必然重新构造** manifest，所以
**打开一道在 UI 之外创建的题目时，保存会用一份不完整的 manifest 覆盖已存的那份**——静默丢掉
`args`/`stdin`/`comparison`/`files`。而 B1 的测试**全绿**，因为前端正好是那个会重新构造的客户端。

`CHG-0025` 裁决新增 `GET /api/v1/questions/{question_id}/draft-input`（课程写权限），
返回与 `QuestionDraftInput` 同形、**减去 `cases`/`reference_answer`** 的内容——
"取回什么就得回发什么"因此成为字面事实。

**明确否决"客户端自己缓存本会话提交过的 manifest"**：那会变成**每个客户端各自维护的一份服务端状态
影子副本**，立刻产生"谁的副本才是对的"，每个新客户端都要再实现一遍，**未缓存过的题目仍然静默降级**
（缺口只是被推后），而且它**没有契约可校验**，因此没有任何门禁能发现它错了。
**缺口的正确位置是契约，不是每个调用方的内存。**

`adapter.files[].content_sha256` 保留在响应里（不返回摘要则 `kind=file` **永远无法回发**），
但把已知代价**写进契约**：短内容的摘要可爆破，因此要求课程写权限、不向学生开放。
**这是一处被明确接受的残余风险**，并写明了另一种选择的代价更大——把可用性缺口换成静默改坏数据。

#### B. 一条新的版本纪律：**`0.15.0` 从此刻起冻结**

`0.15.0` 本轮被修订过**三次**（`CHG-0023` 补输入端、`CHG-0024` 修 `published_at` 可空性、
以及 `CHG-0021` §2.7 的补写）。三次都成立，因为**当时没有任何实现针对它写过代码**。

**但现在已经不行了**：B2 正在实现 `CHG-0019` 的端点、B3 正在实现 `CHG-0023` 的端点——
**它们正在按 `0.15.0` 写代码**。此时再就地修订就是不诚实的：实现者测试的是**摘要 A**，
而契约会变成**同版本号的摘要 B**，此后没有任何东西能区分两者。

因此 `CHG-0025` 排到 **`0.16.0`**，等在飞的实现落地之后。

> **判据**：**"是否可以就地修订"取决于"有没有实现已经针对它写过代码"，不取决于"它是不是刚发布"。**
> 这条判据此前没有写下来过，而我在同一个会话里靠它做了三次修订、又差点做第四次——
> 写下它，是因为下一次做这个判断的人（可能还是我）需要它，而不是需要重新推一遍。

### 3.21 `CHG-0023` 在真实宿主上验证通过；`409` 裁决；以及集成库的探针痕迹

#### A. `CHG-0023` 的真实宿主读数（B3 交付，389 passed / +7 例）

B3 在 18001 起了一个真实 composition host（同一 `run_host.ps1`、同一 `.env.integration`、真实 JWT）：

```text
service        -> 200  keys=[adapter_kind, content_digest, language, question_version_id, reference_answer]
                       language=bash adapter_kind=stdin digest=64 字符 answer=134 字符（正文不打印）
teacher        -> 403        student -> 403
no token       -> 401        unknown token -> 401
service digest == version DTO digest: True          ← §8.5：与 CHG-0008 存证绑定的是同一个摘要
unknown version -> 404                              ← §8.4
no-answer draft -> 409 reference-answer-missing      （its own DTO -> 200，证明资源在、不是 404）
```

**本端点的核心断言是"教师与学生都读不到参考解答"，403 那两行就是它。** 而"服务令牌 200"紧随其后，
证明前面的拒绝来自**授权规则**而不是端点不可用——**一个只测拒绝的测试，在端点是坏的时也会通过**。

B3 还报告了三处设计取舍，其中一处值得记：**响应的投影只能落在 `dto/internal.py`**——
公开出口 `serialize_public` 对键名 `reference_answer` **直接抛错**（那正是保护学生的闸门），
而 `dto/wire.py` 被一条测试逐 `ast.Dict` 扫描受保护键名（evaluation package 绝不允许长出该键）。
**所以"允许说出这个受保护词汇"的模块集合是被工具的，不是靠自觉。**

#### B. `CHG-0017` 的 host 复测：B3 拿到了比我更强的读数

B3 的读数：`content_digest before == after = ea54c6fc…`，并指出**摘要覆盖用例与参考答案，
所以"逐字不变"就等于"两者都还在"**——这比我的"分表摘要逐字相同"更凝练，两个读数不矛盾。

#### C. 裁决：`reference-answer` 的 `409` 覆盖两种情形 → `CHG-0026`

B3 按规程停下上报：契约的 `409` 说明**含糊而非缺失**——第一句收窄到"has **no** reference answer"，
第二句又放宽到"**its state** is what makes verification impossible"，**两句同段，读者必须猜哪一句是判据**。

它多覆盖的第二种情形是：**版本有参考解答但没有用例** ⇒ `content_digest` 为 `null` ⇒ 存证无从绑定。
那是 `CHG-0017` 之后**新可达**的状态（首次写入省略 `cases`）。

**裁决：保留 `409` 覆盖两种情形，只把描述点名**（`CHG-0026`，排 `0.16.0`）。
**不**收窄成"情形②返回 `200`"——该端点服务验证路径，而情形②下验证**必然无法完成**；返回 `200`
只是把失败**推后**到取包或存证处，那里的报错会更难归因（调用方拿着一份合法的参考解答，
却在几步之后因为一个与他刚拿到的东西无关的原因失败）。`409 Conflict` 的语义正是"与资源当前状态冲突"。

> **第五次同一形状**（前四次：`CHG-0017`、`CHG-0021` §2.5、§2.6、`§6.2` 第 1 项）：
> **没有一次是"实现错了"，全都是"契约少写了"，而每一次都让消费者必须猜。**
> 而这一次是**实施侧停下上报的**——它没有自行扩大 `409` 的定义，也没有为了让实现符合契约而改行为。
> **含糊的契约不该由实现去裁决。**

#### D. 集成库的探针痕迹（接受并记录，但下一次要注意）

到本轮为止，验证过程在集成库留下了：

| 来源 | 痕迹 |
|---|---|
| B3 | `81e746e7…` 标签 `unix` + 草稿 v2 |
| 我（覆盖度重叠验证） | `033346f4…`/`08238ead…`/`0a137c88…` 各两个标签 + 各一个草稿版本 |
| B3（CHG-0023 探针） | 两个草稿题目：`CHG-0023 probe: draft with no reference answer`、`CHG-0017 probe: title after the correction`（标签 `probe-chg0017`） |

**处置：接受，不清理。** 契约**没有 DELETE 入口**（草稿删不掉），所以清理本身需要越权操作；
而这些痕迹把覆盖度从空态变成了**可演示**（多标签、计数不等、且有重叠——正是该视图要展示的东西）。

> **但下一条纪律要写下来**：**能用既有题目验证的，就不要新建题目。** 每建一个探针草稿都会永久留在库里，
> 而"永久"在这里是字面意思。B3 本轮为观察两类 `409`/`200` 各建了一个草稿，那是对的（既有题库里
> **没有**"无参考解答"的版本，构造不出来），但这类构造应当尽量落在**一个**题目上而不是每观察一个状态建一个。

#### E. 版本状态

`CHG-0025`（草稿内容读出口）与 `CHG-0026`（`409` 点名）**均为"已采纳，实施排期"**，同批升至 `0.16.0`。
**`0.15.0` 已冻结**（`CHG-0019`/`CHG-0023` 的实现正在针对它写代码）。

### 3.22 **真实 DeepSeek provider 的端到端演示（RQ-T-10 出题轨）—— 经真实 composition host，ALL PASS**

目标第 (5) 项要求"以真实 DeepSeek provider 完成端到端演示"。`advisory_score`（RQ-T-30）在本环境
**做不到**：它需要 `case_evidence`，而 Windows 无沙箱 ⇒ 没有 `completed` 运行 ⇒ 没有证据 ⇒
`max_score` 无从填入（§3.15 前的实测清单）。**但出题辅助（RQ-T-10）不需要评测证据，只需要一道题**
——所以它是这台机器上**唯一能做完整端到端演示**的 AI 面。

脚本：`MAIN/.runtime/e2e_authoring_provider.py`（真实 HTTP → 真实宿主 → B3 → 真实 provider）。

```text
空 topics -> 422                                    ← 提示面是枚举，因此"让它做没文档的事"不可表达
POST /questions/{id}/authoring-assistance  {topics:[quality,wording,difficulty], notes:…}
-> 200   elapsed=9.4s
provenance = {"provider":"deepseek","model":"deepseek-flash","prompt_version":"b3-authoring-v1"}
disclaimers = 3   （含 "Advisory only: accepting a suggestion means you edit the draft yourself,
                    and you remain the author of record."）
suggestions = 8   （quality×4 / wording×3 / difficulty×1，全部 confidence='advisory'）
递归键遍历 31 条键路径：**无一条**命名受保护内容
再读一次题目：**逐字未变**（建议不是编辑）
```

**模型的建议是实质的，不是复述题干**——它指出：题干只有一行占位符（"Probe fixture for the
omission-preserves path."）、标题含探针元语言（`probe`/`after the correction`/变更号）、
`adapter=stdin` 却没有 `stdin` 契约、因此难度**无从估计**。这是对一道真实题目的真实推理。

**为什么这条读数值得单独记**：它同时证明了四件事，且每件都有可核对的判据——
① 真的出网（9.4s，桩会在毫秒级返回）；② `provenance` 点明 provider/model/prompt 版本；
③ `ADR-0009` 的咨询边界**写在响应里**（三条免责声明）；④ 响应**结构上**没有受保护内容的字段。

#### **我在做这条验证时犯的两个错误（第二个必须记）**

1. **猜错了建议项的字段名**：我用 `text`/`suggestion`，实际是 `message`。于是打印出 `len=0` 的
   空摘要——**如果我不看输出就写报告，我会报告"返回了 7 条空建议"**。空摘要这种形状本身就是信号，
   它逼我去核对字段名。
2. **更严重的一类**：我用 `"cases" in json.dumps(reply)` 判"响应不含受保护内容"，
   结果匹配到**模型散文里**的 "over two cases"（"over two cases" 出现在它讨论比较策略的那条建议里），
   于是脚本报了一次**不存在的泄漏**。

> **根因：我的检查测的不是断言所声称的东西。** 契约的原话是
> "The response schema has **no field** for protected content"——那是**结构性**断言，
> 而"那个词在整串 JSON 里不出现"是**另一件事**：它既会漏（字段名换一个写法就绕过），
> 也会误报（模型自己在散文里提到那个词）。
> 已改成**递归遍历所有键路径**再判定，并把这段教训写进脚本注释，
> 以免下一个读脚本的人重新写一遍那个错误的检查。
>
> **这一条比它看起来重要**：它差一步就变成"报告一个不存在的安全缺陷"。
> 那种错误与"漏报"不同——它会让别人去修一个没坏的东西，而修的过程恰恰可能引入真缺陷。

### 3.23 B1 第二个工作包交付（32 文件 / 262 用例）；缺口 11 裁决 → `CHG-0027`

#### A. B1 的读数与门禁

```text
generate-api-types --check（重生成前）  -> 报漂移（common/b2/b3/b4），exit 1   ← 预期
generate-api-types                      -> exit 0
（期间我又落了两次文本：CHG-0021 的四处、CHG-0024；--check 第二次只报 b2/b3）
generate-api-types --check              -> "API types match MAIN/contracts."  exit 0
eslint --max-warnings 0                 -> exit 0
tsc -b                                  -> exit 0
vitest run                              -> 32 files / 262 tests passed
vite build                              -> exit 0
31 文件/240 -> 32 文件/262（净增 22）
```

**摘要链与版本号本身就是一条证据**：`9ae4ebfe… → 511f27ad… → 29271ee2…`，而**版本号始终 `0.15.0`**。
B1 两次都选择**重新生成类型**而不是改断言——这正是契约文本变更后唯一正确的动作。
（如果它改了断言或锁，这条链就读不出来了。）

交付里值得记的三点：
- **删除了被取代的 `PublishRequirement.tsx` 与 3 条 i18n 文案**（不留兼容层），
  并删除覆盖度里**已不可达**的防御分支，把取舍理由写进代码注释。
- **"全通过但 `attestation_id: null` ⇒ 仍不可发布"** 有专门断言——`CHG-0019` 最容易读错的一条。
- 环境态的文案**不含"成功"也不含"验证失败"**（有反向断言），且混合失败不整体归因为环境。
- 在 `phase-02 §11` 加了**取代指针**而不是覆写原文。**历史读数不该被改写。**

#### B. 缺口 11 的裁决 → `CHG-0027`（排 `0.16.0`）

B1 上报：`reason_code` 的类型仍是 `string`，19 项词表只在 `description` 表格里，
所以它只能把可归属的那对值**手抄进 TypeScript**——**这正是 MAIN 硬规则 3 禁止的"复制字段表"**，
而它成为例外只是因为契约没给机器可读形式。**"没有形式"不是"允许复制"的理由。**

裁决**分两半，故意不是"全给机器可读"**：

| 项 | 裁决 | 理由 |
|---|---|---|
| 响应字段 `reason_code` | **加 `enum`（19 项）** | B2 的 `EmailIntakeReason` 已是这 19 项 ⇒ 又一个"实现已对、契约少写"（`CHG-0022` 同族） |
| 可归属的那对值 | **留在散文里，B1 继续手写——明确接受** | 做成 `x-` 扩展会得到**一份没有任何工具核对的"机器可读"副本**（权威在 B2 的 Python 元组里，`check_shared_vocabularies()` 够不着）。**散文会让人去读实现；假机器可读会让人信任它。** 要机器可读，正确机制是**加一道门禁 AST 读取那个元组**——"两份副本 + 一个核对者"，而不是只多写一份副本。已记为选项 |
| 查询参数 `reason_code` | **刻意不加 `enum`** | 那会把"用不存在的码筛选"从**返回空页**变成 **`422`**——是**行为变更**而非补声明，需要 B2 改实现。**记为待决，不夹带进补声明的 CHG** |

> 第三条值得单独记：**它是我第一次主动把"顺手可以做的改进"挡在一个 CHG 之外**，
> 理由是它会把"补声明"变成"改行为"。前几轮我多次在同一批里混入多件事，
> 结果就是 `§3.16` 那种"变更文件清单照计划写"的错误有了可乘之机。**一批变更只做一类事。**

#### C. 缺口 8/9 的收口

B1 确认：缺口 8（`adapter` 无读出口）→ `CHG-0025`；缺口 9（`CHG-0021` 四处文本缺失）→ **在本批内由我补齐**，
B1 抓到了它引起的两次类型漂移。**缺口 8/9 关闭，10（分支不继承 fixture）与 11（本条）为新增登记。**

### 3.24 `§2.5.2` 的裁决：`ADR-0016`（`package_digest` 冻结产物）；以及两处索引遗漏

#### A. 裁决内容

`§2.5.2` 自本轮起一直标着"需要一个裁决"，而我在 `CHG-0023` 的 A1/A2 取舍里**正是用它作为决定性论据**
（否决"把参考解答塞进 evaluation package"，理由之一就是它会改编译器输出、从而让已绑定进历史结果的摘要全部失配）。
趁事实还热，把它定下来：**[ADR-0016](../../adr/ADR-0016-package-digest-freezes-the-artifact.md)**。

**决策**：发布时**冻结编译产物**并服务那份产物；读时校验改成 `digest(served_bytes) == recorded_digest`。
`content_digest`（覆盖题目内容）保持原义——两个摘要从此各管一件事：**内容** 与 **产物**。
"编译器是否仍产生同样的输出"搬到**构建期**（黄金夹具测试），因为那才是它该被响亮发现的地方。

**四个备选方案与否决原因**（ADR 按该目录的要求必须写出被评估过的备选）：

| 方案 | 否决原因 |
|---|---|
| 重算并改写记录值（`repair_package_digests.py` 做的） | **静默重绑证词**；且每次改编译器都要再跑一遍，把一个不变量降级成运维杂务；若摘要已绑进一条已记录的结果，改写它会让那条历史**再也无法被验证** |
| 冻结编译器的规范输出（永不动它） | 不可执行；禁止正当修复，且与"不留兼容层"直接冲突 |
| 给摘要方案加版本（`package_digest_v2`） | 保留了错误前提（"读时重算是一次有效检查"），只是多一个摘要去解释它 |
| 读时只比 `content_digest` | **完全停止**校验"服务出去的字节与发布时一致" |

**一条必须写明的遗留风险**：决策之后**读时不再发现"编译器变了"**。那是**代价不是疏忽**——
因此黄金夹具测试成为**唯一**守卫；若它被削弱或跳过，一次改变输出的编译器回归会**静默发布**。
必须把它写成"改期望摘要需要显式理由"的形式，而不是一条随手可改的快照断言。

**另一条必须记成"运气"的东西**：更早那次改写是**不可逆**的。本次部署中没有证据因此丢失
（集成库 8 条运行全部 `failed`、`evaluation_results` 为 0，**没有任何摘要曾被绑进一条已记录的结果**）。
但那是运气：换一个已跑过成功评测的部署，同样的操作会毁掉历史可核验性。

#### B. 为什么**不**现在实施

它需要**一次 B3 迁移**，而宿主此刻**已经因为 B2 的路由声明而不可重启**（§3.19 第 24 行）。
**把第二件需要停机的事堆进同一个窗口，只会让"能不能重启"这个问题更难回答。**
已记为排在当前集成窗口之后；ADR 的"状态"是**已采纳**，实施是独立工作包。
（`ADR-0016` **不需要 CHG**：`evaluation-package.schema.json` 从未要求"重算"，B2 只读取摘要，
因此不改契约——只改 B3 存储与校验的位置。）

#### C. 两处 MAIN 自己的索引遗漏（顺手修，属同一类"记录了但没落地"）

1. **`ADR-0015` 从未进入 `docs/adr/README.md` 的清单表。** ADR 目录的流程第 4 步明写"更新本文件的清单表"，
   而我上轮创建 ADR-0015 时漏了它——**所以它此前只存在于文件系统里，不在索引上**。
   本次把 ADR-0015 与 ADR-0016 一并补入。
2. **`docs/innovation/inventory.md` 的指引行写着"把契约推进到 `0.13.0`"**，而它早已不是当前值。
   已改为 `0.15.0`，并在同一处写下**为什么它必然会过期**：契约版本由 `contracts.lock` 与
   `CHANGELOG.md` 共同声明，**不要在第三处复述它**。这与我此前"在 `CHANGELOG` 里复述 GIN 断言"
   是同一类错误：**同一事实的第二份副本会独自漂移。**

### 3.25 **MAIN 门禁首次全绿**；宿主重装重启；并在真实端口上撞出 `CHG-0019` 的第三个缺口

#### A. MAIN 门禁 15/15（目标第 (1) 项的"修复红门禁"达成）

B2 实现完毕 → 我用正确形式重装（`uv sync --all-groups --extra modules --reinstall-package autograder-b2`）
→ 只剩 `check_document_encoding_and_links` 报 B2 两份 phase 文档里 `../testing/cases.md`
应为 `../../testing/cases.md`。B2 **已结束**，那两处成了**陈旧产物**（不是并发写入），因此由我代修并告知。

```text
contracts, manifests and requirements: ok (29271ee2480c6e13b896268b02c38a21e290476d1feee7caff777f35cd9521ba)
module documentation governance and links: ok
migration heads and local ports agree with compose: ok
exit 0   ← 15/15 门禁全绿
```

#### B. 宿主重装重启；探针两个方向差集首次同时为 0

```text
host assembled: 49 route paths      （47 + CHG-0019 与 CHG-0023 两个新端点）
declared but NOT served (0): none
served but NOT declared (0): none
restart-ready: every loaded module reports healthy
/health/ready -> ready=true ; B4/B3/B2/B5 全部 healthy=true
openapi.json 里出现 /api/v1/reference-verifications 与
              /api/v1/question-versions/{question_version_id}/reference-answer
```

#### C. 真实端口验收：**两个 PASS，一个致命缺陷**

```
POST /reference-verifications  无令牌  -> 401   ✓
POST /reference-verifications  学生    -> 403   ✓
失败的验证零存证：b3.reference_attestations 前后都是 0   ✓
POST … 已发布版本 -> 503 "B3 request failed: TimeoutError"   耗时 10.03s
GET  /questions/coverage（无外呼的基线）-> 200  0.05s
直接带服务令牌调 B3：evaluation-package 200 (994B)、reference-answer 200 (525B)，均 <1s
```

**确诊：自调用死锁。** `10.03s` 正是 B2 配的包超时 `10.0s`，而同一个 B3 端点从外部调不到 1 秒
⇒ 那个嵌套请求**从未被服务**。机制：`api/reference_routes.py:55-56` 是 `async def`，
而 `:77` 的 `runtime.references.verify(...)` **同步阻塞**，经 `B2_B3_BASE_URL = http://127.0.0.1:18000`
请求**正在服务这个请求的同一个宿主进程** ⇒ 事件循环被占住 ⇒ 自我等待到超时。

**为什么以前没暴露**：评测链的取包在**独立的 B2 worker 进程**里（不在宿主的请求路径上），
同一条 `B3Endpoint` 配置在那边是好的；**这个端点是第一个在宿主请求路径内同步回调宿主的路径。**
**为什么它的 49 项测试没抓到**：抓不到——需要"真实宿主 + 真实自调用"。**这一类缺陷只有真实端口能发现。**

> **我的第一版验证脚本差点把它读错**：我写的是"已发布版本应**越过两次取数**、停在沙箱"，
> 于是把 `503` 当成"沙箱不可用"的证据。真正让诊断成立的不是状态码（两次失败都是 `503`），
> 而是**那两条断言**：detail 必须指名**沙箱**（它没有，它说 `TimeoutError`），
> 以及随后补做的**耗时对比**（`10.03s` vs 基线 `0.05s`）。
> **同一个状态码可以是两件完全不同的事的原因——这正是"读数正确、归因错误"的又一次现身。**

#### D. 缺口 ⑧ 与 `CHG-0019` 的真实状态

B2 在真实 B3 上发现：`getEvaluationPackage` 对**草稿** `409`，而 `recordReferenceAttestation`
**只接受草稿** ⇒ **"验证草稿 → 存证 → 发布"依然不可能**，教师依旧卡在发布
——**而这正是 `CHG-0019` 存在的唯一理由**。已裁决为 `CHG-0028`（**让草稿也能取包**；
`created_at` 用内容时间戳、跳过摘要比对），**B3 改实现**。

**`CHG-0019` 的状态因此被改为"实现已落地、目的未达成"**，其 §7.2/7.3/7.4 在 `CHG-0028` 落地前
**无法执行**（已在 §7 顶部显式标注，而不是留在那里看起来像"待跑"）。`ADR-0015` 也补了 §5
"前置条件"——它的决策 1 单独看是**不可执行**的。

**我的责任，两处同型**：`CHG-0019` §2.4 先写"取评测包（含参考答案）"（那件东西不在包里），
再写"取评测包（隐藏用例与期望输出）"（东西在，但**我只核实了代码件存在，没核实它的前置条件**）。
**同一个设计里我犯了两次同型错误，两次都是实施侧撞出来的。**

> **一条应当被记住的判断**：核实一个**可复用的组件**存在，与核实它**在当前场景下可用**，是两件事。
> 前者读代码就够，后者必须问"它的前置条件我满足吗"。

#### E. 本轮同时定下两件事

- `ADR-0016`（`package_digest` 冻结产物）——见 §3.24。
- **`0.15.0` 的实现已全部落地**（B2 的 `CHG-0019`、B3 的 `CHG-0023`），因此
  **`0.16.0` 批次现在可以开始**（`CHG-0025`/`0026`/`0027`/`0028`），届时一次升版、一次重算、一次重装。

### 3.26 **浏览器端到端**（一直"未执行"的那一类）＋事件链复核；并发现一条**说假话的界面横幅**

#### A. 事件链与静态资源

```text
b2.outbox_events   unpubl=0  publ=31   attempts=1  dead=0
b3.event_outbox    unpubl=0  publ=103  attempts=1
b4.outbox_events   unpubl=0  publ=2    attempts=1
B4_DLQ messages=0        DOMAIN_EVENTS messages=134 bytes=84722        ROSTER_IMPORT messages=2
GET /app -> 1320 bytes, title "AutoGrader 教学评测平台", 脚本 /app/assets/index-CRxKPVIg.js
```

三个 outbox 全部清零、`attempts=1`、`dead=0`、DLQ 为空 ⇒ **中继与 worker 确实在跑**（目标第 (1) 项
"启动三个中继与 worker"）。`b3.event_inbox` 0/0 是**一致的**：它消费 `evaluation.completed`，
而本平台没有 completed 运行。静态资源那条确认**宿主提供的是 B1 刚重建的那份产物**（同一个哈希）。

#### B. 浏览器端到端：覆盖度视图在真实宿主上成立

用真实浏览器打开 `/app/questions`、选课程，**播放器以外的证据全部来自运行中的系统**：

```text
页头: 契约 0.15.0（构建期从 contracts.lock 注入）
coverage-total-value=102   coverage-tagged-value=4   coverage-untagged-value=98
tag_rows: file-systems=3   virtual-memory=3   unix=1
identity: "合计校验：已标注 4 + 未标注 98 = 102，与 total_questions 一致。"
```

**与 §3.18 二的独立 SQL 交叉核对逐项一致**，且并列的 `3,3` 按 `tag` 升序打破
⇒ **顺序确实来自服务端，前端没有重排**。它把三条纪律同时证成了：恒等式、只含计数、顺序不重排。

#### C. 同一屏上发现一条**假话**：`coverage-declared-contract` 横幅

浏览器实测：该横幅 `banner_present: true`，文案是

> "按契约声明实现（CHG-0020）：端点已在 MAIN/contracts 成文，**B3 尚未实现**。因此本页的证据只有生成类型
> 与契约夹具，**没有对着运行中的 B3 跑过**。"

而**同一个面板正在渲染 B3 的实时数据**。**这条横幅自己否定自己旁边的内容。**

它写的时候是对的（那时 B3 确实未实现），但它是**无条件的静态文案**，所以实现一落地它就变成误导。
**这不是功能缺陷，是"界面对用户说了不成立的话"——而后者在本项目里更严重**，
因为功能缺陷会被测试或门禁抓到，而**界面上的一句假话没有任何东西在盯着**。

**为什么它的 240 → 262 项测试全都抓不到**：组件测试用的是契约夹具，而这条横幅**是无条件的**，
因此在夹具世界里永远"正确"。**这一类缺陷只有"真实浏览器 + 真实后端"能发现**——
这正是 §4 里那两条"浏览器端到端"一直记着"未执行"的原因。

**处置**：已派 B1 修改。要求二选一：**删除**，或**由数据驱动**（只在端点真的不可用时显示，
且措辞不再谈"实现进度"而只说"本部署未提供该端点"）。**明确禁止**把它改成"B3 已实现"这种新的静态断言
——它会以同样的方式再次过期。**关于"对方实现到哪一步"的断言不属于界面**：前端无法也不该知道。

#### D. 这一步补上了 §4 里两条一直未执行的验证

第 17 行（覆盖度视图在浏览器里渲染）与第 26 行（`RQ-T-10` 在浏览器里触发）中的第一条**已执行**；
第二条（出题页触发 AI 辅助）仍需单独跑。**并且它证明了这类验证不是形式**——
它抓到的是一条横幅，而不是一行崩溃日志。

### 3.27 浏览器端到端（二）：**出题辅助轨**在真实界面里成立；以及一处"推断过宽"（不是假话）

#### A. `RQ-T-10` 出题辅助：真实浏览器 + 真实 DeepSeek

在 `/app/questions/90001a8f-…/edit` 点"请求建议"：

```text
provenance:  "来源：provider deepseek · model deepseek-flash · prompt b3-authoring-v1"
disclaimers: 3 条原文照录
             （含 "Advisory only: accepting a suggestion means you edit the draft yourself,
               and you remain the author of record."）
建议（7）全部 confidence='advisory'，表格三列（方面 / MESSAGE / 置信度）
```

**建议是实质的、针对这道题的真实内容**：它指出题干只是夹具/管道文字、`adapter=stdin` 却没有输入契约、
`ignore_trailing_whitespace` 与 `numeric_tolerance: null` 的组合会拒绝合法的数值渲染。
`ADR-0009` 的边界在界面上**可见**：免责声明 + 双轨说明 + `advisory` 标签三重呈现。

**这一条同时是目标第 (5) 项"以真实 DeepSeek provider 完成端到端演示"的界面侧证据**
（§3.22 是端点侧的同一件事）。

#### B. 验证面板：死锁在界面上被如实暴露

点"请求平台验证参考答案"（此时 B2 正卡在它自己的自调用死锁上，§3.25 C）：

```text
面板: "…验证需要 Linux 沙箱环境（ADR-0004 禁止把沙箱降级为裸进程）…"
      "服务端说明：B3 request failed: TimeoutError"
content_digest: ea54c6fc…   （与 B3 的读数逐字一致）
控制台 error: 2 条，都是那条预期的 503（浏览器记录失败响应 + 应用记录 problem），无其它
```

#### C. 一处**推断过宽**——但它与 §3.26 C 的横幅**不是同一件事**

`governance.md` 把 `503` 定义为"依赖不可用（readiness 失败的原因）"，而它**同时覆盖两种原因**：
① 本平台没有沙箱；② **某个上游依赖调用失败**（今天正是这一种）。**从状态码本身无法区分**，只能读 `detail`。
B1 的面板对任何 `503` 都给出"验证需要 Linux 沙箱环境"的结论——**其中 B2 的调用失败被读成了环境缺失**。

**但 B1 照原样渲染了服务端的 detail**（"服务端说明：B3 request failed: TimeoutError"），
所以读者能看到真实原因。**因此这是"推断过宽"，不是"说假话"。**

> **这个区分必须写下来**，因为它决定处置的严厉程度：
> **§3.26 C 的横幅是无条件地断言一个已经不成立的事实**（且没有任何原始证据在旁）；
> **这一条是从状态码推出的结论，且把原始证据一并呈现**。前者必须删或改成数据驱动，后者只需让结论不超出证据。
> **把两者当成同一件事，会导致对后者过度反应**（例如把一条本来诚实的呈现方式整段删掉）。

已建议 B1 二选一（标题中性化，或从 `detail` 读原因），**并要求保留"服务端说明：<原样 detail>"那一行**——
它是这条信息诚实的原因。B2 修完死锁后，`503` 在真实环境里会重新只意味着"没有沙箱"，
因此这条优先级低，属"让结论不超出证据"而非"修一个正在骗人的东西"。

### 3.28 自调用死锁**已修**（耗时 10.03s → 0.05s）；缺口 ⑧ 与 `CHG-0026` 情形②在真实端口上同时确认；并新发现一处 `422` detail 说假话

#### A. 修复确证（这次是**耗时**，不只是状态码）

```text
POST /reference-verifications  已发布版本 34d07765…（有答案、有用例、语言匹配）
-> 503   耗时 0.05s        ← 修复前 10.03s
   detail "the sandbox is unavailable on this platform, so the reference answer was not executed
           (no attestation was recorded)"
零存证（前后都是 0）    响应无受保护内容    无令牌 401 / 学生 403
```

detail 从 `B3 request failed: TimeoutError` 变为**指名沙箱**，且耗时降到基线的量级
⇒ **它越过了取包与取参考解答两跳**。这条读数同时回答了"已发布版本能走到哪一步"。

#### B. 直接问 B3，两个 `409` 都可区分（**缺口 ⑧ 与 `CHG-0026` 情形②同时确认**）

```text
草稿 626fc91c…（它确实有参考解答，语言也与版本声明一致）
  evaluation-package -> 409 "a draft version has no evaluation package; publish it first"   ← 缺口 ⑧
  reference-answer   -> 409 "this version has no content digest yet, so an answer executed
                             against it could not be attested: a version needs at least one test …"
                                                                        ← CHG-0026 情形②，措辞确实点名了
已发布 34d07765…
  evaluation-package -> 200 (1016 bytes)      reference-answer -> 200 (525 bytes)
```

**缺口 ⑧ 不再是"B2 读代码的推断"，而是真实端口上的读数**；而 B3 的 `CHG-0026` 收口
（两条 `409` 的 detail 可区分）也在真实响应里成立。

#### C. 新发现：B2 的 `422` detail **断言了一个不成立的原因**

对同一道**确实有参考解答**的草稿，B2 返回
`422 "the question version exists but carries no reference answer to verify"`——
**与 B3 给的两个原因都不符，而且对这道版本是假的**。

**为什么必须改**：三种情形要求教师做**完全不同**的事（先发布 / 先加用例 / 去写答案），
而一句概括的原因**让教师无法决定下一步，且在第一种情形下还是错的**。已派 B2 改成
**带出或转述 B3 的原因**，不得替换成自己构造的、断言了具体原因的句子。

> **这是"推断过宽"家族里最严重的一处**，因为它在**服务端响应**里，而不是界面文案里：
> API 的 `detail` 是契约级的信号，下游（包括 B1 的界面）只能照它转述。
> §3.26 C 是界面无条件断言、§3.27 C 是界面推断过宽且附原始证据，**这一处是 API 主动给出了错误的因果**。

#### D. **我第三次犯同一类错误**：用粗糙子串匹配冒充针对性断言

我的验证脚本里写了一条断言："detail 不得提到 package 或 reference answer"。
修复后 B2 的 detail 是
`"…so the **reference answer** was not executed (no attestation was recorded)"`
——它**提到**参考解答，却在**正确地归因于沙箱**。于是脚本报了一次**假失败**。

**这是本会话第三次**：`CHG-0022` 验证时 `"cases" in json.dumps(reply)` 匹配到模型散文
（§3.22 D-2）、`CHG-0019` 验证时把 `503` 一律读成沙箱（§3.25 C），以及这次。
**三次的形状完全相同：我测的不是断言所声称的东西。**

**由此定下一条可执行的规则**：
**断言必须打在区分两种情形的那一个信号上**（这里是"有没有上游失败标记"），
**而不是打在某个词是否出现上**。已把这条规则与本次的反例写进脚本注释，
因为**下一个读脚本的人会重新写一遍那个错误的检查**。

**同一次还修了第二个脚本缺陷**：草稿夹具的查询没有要求"答案语言 == 版本声明语言"，
于是它选中的草稿触发的是 B3 的 `409`（无摘要）而不是缺口 ⑧ 的取包 `409`——
**一个"名字叫缺口 ⑧ 验证、实际测的是别的东西"的验证**。夹具选择也是断言的一部分。

### 3.29 契约 `0.15.0 → 0.16.0`：让发布闭环可执行的三处声明补全（`CHG-0026`/`0027`/`0028`）

#### A. 为什么是这三条，而不是四条

`CHG-0025`（草稿内容的读出口）**被移出本批**，改排 `0.17.0`。判据是我在 §3.23 C 写下的那条规则：
**一批变更只做一类事**。本批三条**全是"把已成立或必须成立的事实写成声明"**：

| CHG | 它把什么写成了声明 |
|---|---|
| `CHG-0026` | `reference-answer` 的 `409` **两种情形**（原说明同段给了两种读法，实施侧必须猜哪一句是判据） |
| `CHG-0027` | `reason_code` 的 19 项词表变成**机器可读的 `enum`**（原先只在 description 表格里，逼消费者手抄字段表） |
| `CHG-0028` | **草稿也必须能取到评测包**，以及两处 `409` 的含义 |

而 `CHG-0025` 是**新增一个能力**，性质不同。**把它塞进本批，正是 §3.16 那种"变更文件清单照计划写"的错误得以发生的那类混装。**

#### B. 工具读数（摘要仍由工具计算，未手填）

```text
relock_contracts : 0.15.0 29271ee2480c… -> 0.16.0 372cfcabad2ac832a175c35040a57ae0374d2540364f3834086b33bfe7f2b916
sync_contract_version : 9 处（5 份 module.yaml + 4 个 plugin.py）0.15.0 -> 0.16.0
check_contract_parses_strictly / check_manifests / check_change_records : 全部 PASS
```

**我顺手在契约里改掉了另一句假话**：`getEvaluationPackage` 的 description 原本写着
"The response carries reference answers"——而 `CHG-0023` 的核心发现正是**包里没有参考解答**。
**同一句假话曾被我自己当作设计前提（`CHG-0019` §2.4），现在它从契约里也消失了。**

#### C. 刻意造成的红窗口，以及我如何把它压到最小

升版后 `check_module_descriptors` 与 `check_module_installations` 必然变红——**契约版本有第六份副本**
（安装后的描述符），这正是 §3.17 C 记录过的那件事。

处置：
- **B3/B4/B5 空闲** ⇒ 立即重装，它们的红消失；
- **B2 正在改源码** ⇒ **刻意不重装**（重装会把一个在制品的 B2 装进去，并让它自己的验证对着另一个 revision 跑）。

于是两条红**只剩 B2**，且原因写在报告里：`descriptor.contract_version='0.15.0' but module.yaml declares '0.16.0'`。
**B2 结束时重装一次即全绿**——这不是"绕过门禁"，是把一次重装从两次压成一次。

#### D. 已派 B3 实现 `CHG-0028`（发布死结的关键路径）

`CHG-0028` 是**唯一需要改实现**的一条。已派 B3，并把 `CHG-0028` §9 的 6 条验证逐条写进工作包，
其中**第 3 条最重要**：已发布版本**仍然**做摘要比对（改一行受保护内容 → `409`）——
**它是"放宽草稿没有把安全检查一起丢掉"的证据**。另特别指出第 2 条不能实现成 `now()`
（否则同一草稿两次取包摘要会漂移）。

> 至此，**目标第 (4) 项"破除发布死结"的前置条件全部就位**：契约声明了草稿可取包，
> B3 一实现，`CHG-0019` 的 §7.2/7.3/7.4 就有条件执行了。

### 3.30 死结论证的**事实基础**第一次从运行中的系统读出来；并发现一条**指向被取代旧路径**的拒绝信息

#### A. 三条读数（`ALL PASS`，真实端口）

死结的全部推理都建立在这三件事上，而它们此前**只写在文档里**，没有任何人从运行中的系统读过。
`MAIN/.runtime/verify_publish_gate.py` 现在把它们读了出来：

```text
1. 门禁是真的        POST /publish（草稿用例与参考答案齐备，只缺存证）
                     -> 422 "publish rejected: no reference-answer attestation matches this content
                             (content_digest ea54c6fc…)"
2. 教师不能自证      POST /attestations 用**用户令牌**
                     -> 403 "this endpoint is restricted to service-to-service callers;
                             internal is not a substitute for authorization"
3. 存证是核对不是采信 POST /attestations 用**服务令牌 + 错误摘要**
                     -> 409 "the declared content digest does not match this version's content;
                             re-run the verifier against the current content"
4. 全程零存证        该版本 attestations 计数前后都是 0
```

**第 1 条是"死结存在"的证据，第 2 条是"教师无法自己解开它"的证据**——两者合起来就是
`CHG-0019`/`ADR-0015` 的**全部前提**，而此前它们只是断言。第 3 条则证明存证**不是可以随便写的记录**：
它被交叉核对，因此"记一条假的"这条路也走不通。

**这个脚本刻意不写任何真存证**：在本平台写一条等于声称"参考解答被执行过"，而那正是门禁要防的伪造。
**读"拒绝"是诚实的那一半，而它恰好也是死结论证所需要的那一半。**

#### B. 发现：`publish` 的拒绝信息把教师指向**被取代的旧路径**

```text
… Run the question-bank verifier (MAIN/scripts/verify_question_bank.py) against the version,
  then POST its result to /api/v1/question-versions/{question_version_id}/attestations. …
```

来源：`B3/src/autograder_b3/domain/attestations.py:166-171`。

**那是 `CHG-0019` **之前**的路径**——"找一个能跑那个脚本的运维"。而 `CHG-0019` + `ADR-0015`
已把它换成**教师在出题页点"验证并发布"**（B2 经 `POST /api/v1/reference-verifications` 执行）。

> **这条信息出现在教师唯一被卡住的那一刻，而它把他指向脚本与运维，而不是那个已经存在的按钮。**
> **`CHG-0019` 换掉了机制，却留下了指向旧机制的指针。** 这与 §3.27 家族同源——
> **一次成功的替换，其残留物往往不在它改动的地方，而在它**引用**的地方。**
> 已派 B3 修（不需契约变更，这段文案不在契约里）。

**同类的第二处痕迹（在契约里）**：`ReferenceAttestationInput.verifier` 的 description 写着
`"Who ran it, e.g. MAIN/scripts/verify_question_bank.py."`，而 B2 的新流程实际发
`autograder-b2/reference-verification`（`B2/.../reference/b3.py:42`）。**记为 `0.17.0` 的一条描述修正**
（与 `CHG-0025` 同批），**未在本轮动**——`0.16.0` 正在被 `CHG-0028` 的实现消费。

#### C. 我又一次在"输入的形状"上栽了（同一族的第四次，但这次是另一种）

本次脚本连错两处，**都不是系统的行为**：
1. 我给只接受四种字段的 `publish` **发了一个请求体**——而契约里 `publishQuestionVersion`
   **没有 `requestBody`**。
2. 我把 `B2_B3_AUTHORIZATION` 当作"裸 JWT"传给 `call()`，而它**本身已含 `Bearer ` 前缀**
   ⇒ 头部变成 `Bearer Bearer …` ⇒ `401 malformed bearer token`。**那看起来像"服务调用方被拒"，实际是我的头部畸形。**

**第 2 处特别值得记**：我早先用同一个值**成功**调过两个 B3 端点——因为那次我把整串直接放进头部。
**同一个值、两种用法、一个成功一个失败**，而失败的那次差点被我读成授权问题。
已把它写进脚本注释：**读一个输入的形状，再去对基于它的响应下断言。**

（另注：用户令牌那次返回 `403` 而不是畸形 `401`，**与 `CHG-0021` §2.6 记录的 B3"签名校验优先"一致**——
一个意外的一致性印证。）

### 3.31 目标第 (2) 项的"**由 B1 消费**"这半在真实浏览器里逐页验证；B1 把"假话横幅"从一条修成四条

#### A. B1 的发现比我的更大：**四条**假话横幅，不是一条

我上一轮只发现了覆盖度面板上那条。B1 用**只读探测**（无令牌 `curl`，`401`/`405` 即"路由存在"）确认
**四个端点全部已实现**：

```text
GET /questions/coverage            -> 422（缺 course_id，B3 请求校验优先）
GET /intake/quarantines            -> 401
GET /evaluation-runs/quality-summary -> 401
GET /evaluation-runs/{id}/appeal-pack -> 401
GET /reference-verifications       -> 405（POST-only）
```

于是隔离区/证据包/质量三页挂着**同一类假话**。B1 按同一原则一并处理（**只修一条等于故意把另外三条留在页面上**）：
删除四条常驻横幅与两条 i18n 词条；端点不可用改为**由真实 `404` 驱动**，措辞改成
"本部署的这次请求取不到这个端点"（不再提任何模块的实现进度），testid 也从 `coverage-not-implemented`
改名为 `coverage-endpoint-absent`（**名字本身也不再断言进度**）；
四处旧断言改成**反向断言**，并新增一条断言"面板里不出现尚未实现/未实现/契约夹具"——
**用测试钉住"界面不做进度断言"这条规则**。`adapter-not-readable` 被保留，理由正确：
它陈述的是**本页行为**（保存是重新声明 manifest），不是别人的进度。

`503` 的标题也按 §3.27 C 收窄为中性，并**新增说明"本页不从状态码推断是哪一类依赖"**，
同时**保留** `服务端说明：{detail}`（新增 testid），夹具用例特意用我看到的原文断言逐字可见。
263 项测试、五条命令全绿。

#### B. 我在真实浏览器里逐页复核（**独立于 B1 的自述**）

| 页面 | 读数 |
|---|---|
| `/app/quality` | **真实 `quality-summary` 端到端**：`attempt_count=6`、`unmeasured_runs=6`、安全三态**分开**（发现 0 / 检查过未发现 2 / **从未检查 4**）、失败类别（系统原因 0 / 其余 0）、逐用例"暂无数据"、静态规则命中表（`STAT-PY-013` info 3 次，其余 0）。`mentionsNotImplemented=false` |
| `/app/intake/quarantines` | **空列表而非错误**（"当前筛选下没有隔离记录"），无横幅；并把 `CHG-0021` §2.5 的裁决**逐字渲染在界面上**（"本页始终显式发送 `?resolved=…`…省略的语义是「不过滤（全部）」"）⇒ **B1 确实消费了那条裁决** |
| `/app/questions/…/edit` | 出题辅助与验证面板均正常渲染（§3.27） |

**质量页的呈现方式本身值得记**：它把**样本量放在每一项计数的上方**（"先看样本，再看结论"），
`unmeasured_runs` **单独成列不并入**任何安全检查计数，`security_not_checked` 与 `security_not_found`
**分开**——三处都在拒绝同一个错误读法："没有数据"不等于"没有问题"。**那是 `CHG-0015` 那批声明的意图
第一次以界面形态被完整表达出来。**

#### C. 证据包面板：**环境阻塞，且 B1 的标注是对的**

API 层面核过：对**没有报告**的运行，`appeal-pack` 返回 `404` 且 detail 诚实
（"The evaluation run exists but has no report yet (state: failed, 终态原因 infrastructure_error)…"）。
而集成库 `evaluation_results = 0` ⇒ **不存在任何报告** ⇒ 下钻里那个面板**不渲染**。
B1 如实写"本轮没在浏览器里打开，只有组件测试 + 路由探测"，**没有假装观察过**。**这不是 B1 的缺陷，是环境事实。**

（顺带一条：三条运行里两条返回 `403` "The caller is not permitted to perform this operation."，
说明课程作用域判定在真实数据上生效。）


---

### 3.32 `--check` 存在却**没有任何执行点**：补上第 16 条门禁（目标第 (1) 项）

**发现。** 本项目的阶段文档引用套件总数（"1145 passed"、"23 files / 133 tests"），这些数字是"东西能跑"的
主要证据，同时**是手抄的散文**。`scripts/test_evidence.py` 正是为此而写：把每次套件运行记成绑定
"契约版本 + 契约摘要"的物件，并提供 `--check` 判决。它的 docstring 写着 "a gate fails when a module's
evidence is missing or was recorded against a contract the repository has since moved past"。

**改动前，`checks.py` 里没有任何一处引用它**（`grep test_evidence MAIN/host/src/autograder_host/checks.py`
为空）。于是那句 docstring 是**假的**：脚本是*为了*门禁而写的，却从未被任何门禁调用。实测它的判决是：

```text
B1: evidence was recorded at contract 0.7.0 (4ad48fbbd710…) but the repository is at 0.16.0 (372cfcabad2a…) -- re-run the suite
B2/B3/B4/B5: no recorded test evidence
```

也就是说，**文档引用的 B1 数字描述的是 9 个契约版本之前的仓库**（记录里 133 个测试，本轮实测 263），
而四个模块从未有过记录。这份判决向**没有人**报告了整个区间。这是本项目又一次遇到
"**机制存在、执行点缺失**"这一族缺陷（`CHG-0021` 的四处 `b2.yaml` 文本"记录为已落地但从未写入"是同一族）：
**一个没有执行点的机制，与没有机制无法区分。**

**处置：**

1. 判定逻辑抽成 `test_evidence.py` 里的纯函数 `evidence_problems()`（不打印、不抛异常），`checks.py`
   按路径加载脚本后调用它——**只有一份实现**。在两边各写一遍会产生同一仓库上的**两个判决**，
   而它们之间的分歧不可见，这正是该文件要拦的缺陷形状。
2. `checks.py` 新增 `check_test_evidence()`，成为 `main()` 的第 **16** 条门禁（`gate_status.py` 用 `ast`
   读 `main()` 源码取顺序，自动纳入）。它**不**做 `is_file()` 兜底、**不**吞异常：该脚本是 MAIN 自己的，
   加载失败就是门禁失败，而不是"跳过这条检查"的理由。
3. `MAIN/AGENTS.md` 的通过判据由三条变四条；`docs/testing/strategy.md` §5 补该行，并标明那张表是**选列**
   而完整有序清单是 `main()` 自身（`gate_status.py --quiet` 会照它打印）。

**验证（真实读数）：**

| 读数 | 结果 |
|---|---|
| `test_evidence.py --module B1` | `exit=0 {'passed': 263}`，契约 `0.16.0` / `372cfcabad2a…`。**文档引用的 133 与实测 263 的差距至此由机器持有** |
| `test_evidence.py --module B3` | `exit=0 {'passed': 393, 'duration_ms': 50480}`。**这条记录不是本轮作者写的**：我本轮只对 B1/B4/B5 调用过 `--module`，而该物件的 `recorded_at` 是 `11:26:20Z`，晚于本门禁接线（B1 的物件是 `11:20:56Z`）——即 **B3 的实现代理自己跑出来的** |
| `test_evidence.py --module B4` | `exit=0 {'passed': 227, 'duration_ms': 139180}` |
| `test_evidence.py --module B5` | 第一次 `exit=1 {'passed': 23, 'errors': 5}`，诊断后第二次 `exit=0 {'passed': 28}`（见 §3.32.1） |
| `checks.check_test_evidence()` | 首轮报 `B1(0.7.0)` + `B2/B3/B4/B5 无记录`；随后**只剩 `B2`** |
| `gate_status.py --quiet` | **15/16**，`red: check_test_evidence` |

顺带一条**非预期**读数：此前的红门禁 `check_module_installations`（B3 安装副本落后于工作树）本轮已转绿，
说明在飞的 B2/B3 代理重装过模块。**本轮没有为了让门禁变绿而放宽任何判据。**

#### 3.32.1 B5 的 5 个 error 是**这台机器的 ACL**，不是 B5

B5 第一次记录是 `23 passed, 5 errors`，5 个 error 全在 `tests/test_conformance_views.py`——正是需要
`assembled_app` 的那个文件，因此第一嫌疑是"在飞代理把安装副本改坏了"。逐项排除：

| 假设 | 检验 | 读数 |
|---|---|---|
| 安装的组合被改坏 | `scripts/probe_host_assembly.py` | `host assembled: 49 route paths`；`declared but NOT served (0): none`；`served but NOT declared (0): none`；四个模块 `ok`；`restart-ready` |
| B5 自己坏了 | 单独重跑该文件（`--tb=line`） | `E PermissionError: [WinError 5] 拒绝访问。: 'D:\dev\temp\pytest-of-LEGION'` |

即 pytest 的 `tmp_path` 基目录**在工作区之外且本账号不可写**（与 `D:\dev\caches\uv` 的 os error 5 是同一类
环境限制，见 `MAIN/AGENTS.md`）。B4 之所以 227 条全过，是因为它**已经知道这件事**：`B4/tests/conftest.py`
的 `workspace_tmp` fixture 文档里写明"`tmp_path` 解析到工作区之外，因此不是套件可写的位置"。

**受控验证（只改一个变量）**：`uv run pytest -q --basetemp .pytest-tmp`（工作区内的 scratch）→
`28 passed, 2 warnings`，**0 errors**。结论：那 5 个 error 衡量的是机器的 ACL。

**处置**：把子进程的 `TMP`/`TEMP`/`TMPDIR` 重定向到 `MAIN/.runtime/pytest-tmp`（`test_evidence.py` 的
`run_suite()` 内），而不是加一个只覆盖 pytest 自己临时目录的 `--basetemp`。**这份修正必须落在交付扫描
会走的同一条路径上**：若只在我手工重跑时修，`--all` 会永远重新记录那份假失败。

**这条最值得记的不是修正，而是它差点变成什么**：一份 `exit_code=1` 的 B5 证据物件会**永久**断言
"B5 的套件是红的"，而它与 B5 无关；它披着"证据"的外衣，比手抄的数字更难被质疑。本轮因此**先诊断、
再重录**，而不是把这条红当作"B5 有问题"写进报告。

**仍未完成**：交付扫描（`--all`）的**完整五模块**记录。只差 **B2**：其实现代理仍在运行，其套件尚不稳定，
此刻记录下来的会是**即将被它改掉的**代码的数字，故本轮只记录 B1/B3/B4/B5。**门禁因此保持红色，
并把逐模块原因写在消息里**——这是真实状态，不是待抹平的噪声。

**仍存在的真实环境缺陷（本轮不替 B5 决定）**：`D:\dev\temp` 不可写，使**任何**使用 `tmp_path` 的套件
在手工执行时失败。B4 用自家 fixture 绕开，而 B5 的 runbook（`B5/AGENTS.md` 里的 `uv run pytest -q`）
没有，因此按文档手工跑 B5 仍会看到那 5 个 error。要么修该目录的 ACL，要么由 B5 用与 B4 相同的方式绕开——
**这属于 B5 归属方的处置，本轮只记录。**


---

### 3.33 `CHG-0028` 的独立复核；并发现**"运行中的进程"是第三个新鲜度缺口**（目标第 (1) 项）

B3 的实现代理回报 `CHG-0028` 已落地并自查通过。**报告不被采信，逐条回到源头复验**。

**先复验它声称的实现（都成立）：**

| 声称 | 复验方式 | 读数 |
|---|---|---|
| 安装副本与源码树一致 | 四个文件的 SHA-256 对比 | `packages.py D48B28720870`、`versions.py D004AAA62B9F`、`attestations.py 1CBA3D1F7063`、`version_content.py 823C0E53F6C8` —— **tree == installed** |
| 草稿路径不读 `published_at`、不用 `now()` | 读 `domain/versions.py:67-87` | `snapshot_for(created_at=version.updated_at)`，docstring 明写 "never `now()`"；`published_at` 只在**已发布**路径被读，且 `None` 即 `Conflict` |
| 文案修 ① | 读 `domain/attestations.py:172-182` | detail 指向 "verify and publish"／平台 reference-verification 端点，保留 `content_digest` 与"编辑内容使存证失效"；**不再出现**被取代的脚本名 |
| 已发布路径仍与记录摘要比对 | **运行中的宿主 18000** `GET …/34d07765…/evaluation-package` | `200`，`package_digest=1e997bbf…`；库里该行 `package_digest` 正是 `1e997bbf…` ⇒ **相等**；两次调用 `created_at` 与摘要完全一致 |

**再复验它报的两处"数据事实"——两处引用都写错了**（结论对、数字贴错列）：

| 它的写法 | 源头读数 | 判定 |
|---|---|---|
| `626fc91c…` 是"`updated_at == published_at == 11:16:22.582`"的反例 | 集成库该行 `status='draft'`、**`published_at = NULL`**、`updated_at = 11:16:22.582`。`updated_at == published_at` 的是**已发布**的 `34d07765…`（两者均 `13:17:03.824`） | **假**。它把 `updated_at` 抄成了 `published_at` |
| `34d07765…: digest=0c036fc3…` | 库里 `package_digest = 1e997bbf…`、`content_digest = 0c036fc3…` | 标签错：那不是 `package_digest`。**但它"返回摘要 == 记录摘要"的结论为真** |

两条错误的**后果不同**，这正是要分开记的原因：第二条只影响可读性；第一条若被当作事实接受，
会让我去"修一句其实在数据上成立的契约措辞"——**报告的数字引用也必须回到源头核对，只核对结论不够**。
（已把两处发回 B3 归属方修正，因为它落在 B3 的阶段文档里，且该文件第 31 行与第 98–102 行**自相矛盾**：
前者已写"草稿可能有也可能没有"。）

**本轮真正的新发现：第三个新鲜度缺口。**

现有机制覆盖了两格，**没有任何东西覆盖第三格**：

| 保证 | 机制 | 本轮读数 |
|---|---|---|
| 安装副本 == 工作树 | `check_module_installations()` | PASS |
| 用**已安装**的包能组装出完整组合 | `probe_host_assembly.py` | 49 routes；声明未服务 0；服务未声明 0；**restart-ready** |
| **正在服务流量的进程加载的是这份代码？** | **（无）** | **否** |

证据是进程与代码的**时间先后**：宿主 pid 42120（端口 18000）启动于 **`19:03:08`**，而安装副本里
`autograder_b3` 最新的 `.py` 是 **`19:20:05`**、`autograder_b2` 是 **`19:13:39`**。于是三者**同时成立**：
门禁全绿、重启就绪、**而线上服务的仍是旧行为**。表现就是我这次拿到的：

```text
GET …/626fc91c…/evaluation-package -> 409 "a draft version has no evaluation package; publish it first"
```

那是**启动时载入的旧模块**在回答，与安装物件和源码树都无关。**危险在于它的样子**：一份在重启前跑的
"真实端口 E2E"会得到**关于旧代码的**结论，而它与"新代码有 bug"完全无法区分——本项目反复遇到的
"机制存在、但没有覆盖那一格"又出现一次。

**处置（属最终集成序列，本轮不做）**：把顺序固定为 **重装模块 → 重启 host/三个中继/worker → 之后才做
真实端口断言**；并让 E2E 自己把"服务进程的启动时间晚于安装包的 mtime"作为**前置条件**读一次，
否则它的结论不可信。（这不能做成 `checks.py` 门禁：门禁必须在没有宿主运行时也能通过。）

**契约措辞的一处精确化**：`CHG-0028` §5 与 §9.2 原先写"草稿**没有** `published_at`"。那是**存储层不保证**
的全称断言——CHECK 只要求"离开草稿后必须有"（本轮实测：集成库 9 个草稿、B3 本地库 65 个草稿，
带 `published_at` 的均为 **0**，即该断言在数据上**恰好**成立而**不是约束**）。两处已在 `CHG-0028` 内就地
改准（改变更记录**不动摘要**）；`b3.yaml` 里同一句移到 `0.17.0` 批次（已记入 `CHG-0025` §7）——
**单独修它会改变契约摘要，从而让 B1/B3/B4/B5 四份证据同时失效**。


---

### 3.34 重启**证明**了"运行中的进程"就是那个变量；发布工作流在真实端口 18000 上验完（目标第 (1)(4) 项）

**受控实验——把 §3.33 的因果从"时间戳推理"变成"事实"。** 若只留时间戳，那仍是一个未验证的解释；
所以重启宿主并重放**同一个请求**（我本轮只做了"重启"这一件事）：

| | 进程 | 服务进程启动 | 安装包最新 `.py` | `GET …/626fc91c…/evaluation-package` |
|---|---|---|---|---|
| 重启前 | pid 42120 | `19:03:08` | `19:20:05` | **`409`** `"a draft version has no evaluation package; publish it first"` |
| 重启后 | pid 37572 | `19:32:13` | `19:20:05` | **`200`**，`created_at=2026-09-18T11:16:22.582Z`，两次取包 `created_at` 与 `package_digest` 完全一致 |

同一份安装代码、同一个库、同一个请求，**唯一变量是进程**。`fresh=True`（启动时间晚于安装包 mtime）。

**`CHG-0028` 的三条验收由我在真实端口独立验到**（不采信实现方报告）：

| `CHG-0028` | 我的读数 |
|---|---|
| §9.1 草稿取包 `200` 且 `question_version_id` 匹配 | `200`，`qvid` 匹配 |
| §9.2 草稿 `created_at` 是内容时间戳、两次一致 | `11:16:22.582Z` **等于该行的 `updated_at`**（该行 `published_at` 为 **NULL**），`repeat_identical=True` —— **既不是 `now()` 也不是 `published_at`** |
| §9.3 已发布仍与记录摘要比对 | `200`，`package_digest = 1e997bbf…` **等于库里该行记录的 `package_digest`**；`created_at = published_at` |

**发布工作流（目标第 (4) 项）在真实端口上的四个读数：**

| 调用 | 结果 |
|---|---|
| 草稿 + `POST /api/v1/reference-verifications`（教师令牌） | `422` / **109 ms**，detail **逐字转述** B3 的 `409`，且是 B3 **修后**的文案（"this version has no content digest yet … the digest is what binds the answer to the content it was run against"）⇒ **B2 的转述修与 B3 的文案修 ② 同时被真实端口验到** |
| 已发布 + 同一调用 | `503` / **85 ms**，`"the sandbox is unavailable on this platform, so the reference answer was not executed (no attestation was recorded)"` ⇒ **死锁修在真实端口验到**（修复前为 `10.03 s` / `10.11 s`） |
| 草稿 + `POST …/publish`（教师令牌，**无 body**） | `403` `"the requested course is outside the caller's authorization scope"` —— 课程作用域判定在真实数据上生效 |
| 草稿 + 同一 `publish`（**admin** 令牌） | `422` / **53 ms**，`type=…/reference-attestation-missing`，detail 指向 **"verify and publish" 动作／平台的 reference-verification 端点**、保留 `content_digest`、保留"编辑内容使存证失效"、**不出现**被取代的脚本名 ⇒ **文案修 ① 在真实端口验到** |
| 已发布 + `publish` | `409` `"only a draft version can be published (current status: published)"` —— 状态机读数正确 |

顺带一条**容易被误读**的真实读数：`publish` 的拒绝信息报的是 `content_digest 161d415f…`，而该**列的**值是
**NULL**。这不是矛盾——门禁用的是 **B3 从存储内容重算**的摘要（其 docstring 明写"绝不用调用方提供的"），
所以这个草稿在"验证→存证→发布"的意义上是**可发布**的，只是还没有存证。

**这条链在本平台只能走到沙箱为止**：`verify → attest → publish` 的完整闭环要求**真的执行**参考解答，
而 Windows 无 nsjail，因此终点是那个诚实的 `503`。链路上其余每一段（取包、取答案、拒绝、转述、状态机、
作用域）本轮都已在真实端口独立验到。

**本轮的两条裁决：**

1. **门禁强度是有意的。** B2 的实现方提出三条出路（修那几条失败／让门禁容忍"已登记的失败"／给 B2 显式豁免），
   我**只接受"修"**：容忍已登记的失败就是把红记成绿（`strategy.md` §7 禁止的验收伪装），而一条**恒红**的门禁
   会被关掉（该文件 §5 已就 `check_shared_vocabularies` 记过这条）。已授权 B2 修那 4 条，并明令：不许放宽期望、
   不许 `skip`/`xfail`/重试、若某条是产品缺陷（并列排序键下无确定性 tie-breaker ⇒ 分页会漏/重）就**改产品代码**。
   `AGENTS.md` 第四条判据同步改准为 "current **and green**"，并写明不豁免、不容忍。
2. **`checks.py` 的修复提示本身就是错的**（第三次"机制换了、指针没换"，前两次见 §3.33 与 §3.32）：
   它在报 stale 时给出的 `uv sync --reinstall-package <pkg>` 会让 uv 判定四个模块"不再被需要"并**全部卸载**
   （它们是靠 `--extra modules` 装进来的），把 "stale" 变成 "not installed"——B2 的实现方照做后复现了这一点。
   已改为带 `--all-groups --extra modules` 的完整命令，并把这段后果写进提示本身。


---

### 3.35 第 (3) 项双轨边界与第 (5) 项 provider 面的审计读数

**(a) 双轨边界：契约、实现、可见性三处都成立。**

| 层 | 读数 |
|---|---|
| 契约（`0.13.0` / `CHG-0018`） | `EvaluationCommentary.advisory_score` 是**可空子对象**，消费者只能经 `commentary.advisory_score.score` 取到（多一层路径本身就是约束）；`disclaimers` 恒在且**非空**、明确覆盖分数参考（"non-deterministic"、"not part of the score"、"not grounds for appeal"），契约原话："A client that renders `advisory_score` without them is presenting a model's estimate as a score" |
| B2 实现 | 四列 `advisory_*`（迁移 `0014`），表约束保证**同生同死**；`advisory_max_score` 由 `case_evidence.weight` 求和填入、**不由模型生成**，且解析器把模型自带的 `max_score` 判为**失败**（"实现里不存在读模型满分的代码路径"）；重生成**显式清空**上一次的参考分；越界判为生成失败而**不是截断** |
| B1 可见性 | `AdvisoryScoreBlock`（`data-testid=advisory-score`）**只在 `disclaimersPresent` 为真时渲染**——把上面那句契约原话变成代码；`advisory_score` 为 `null` 时整块不渲染且**不用 0 代替**；与权威分**外观互斥**（灰底虚线、`pill--idle`、不同 testId）；文案逐条标注"不是分数：不参与判分、不参与申诉、不进入成绩与导出" |

**隔离是被测试的，不只是被声明的**：`GradeDrilldown.test.tsx` 有一条测试遍历成绩轨道的计算/导出模块源码，
断言其中**不出现任何 `advisory` 引用**；另有测试断言权威分与参考分在 DOM 中的**先后顺序**、以及参考分里
不得出现权威分的类名与 `pill--ok`。

**但"真实数据演示"被环境挡住**（承接 §4 表第 4 行）：`evaluation_results = 0`、`case_evidence = 0`，
库里 2 条 `ready` 评语的 `advisory_score` **全为 NULL**。没有"有证据的运行"就没有可填的满分，
因此"学生/教师看到**非空**参考分"在本平台**无法产生**。**这不是实现缺失，是环境事实**——不得写成已演示。

**(b) 事件链是健康的，而且真的跑过**（目标第 (1) 项）。`scripts/event_chain_status.py`：

```text
b2.outbox_events   unpubl 0   publ  31   attempts 1   dead 0
b3.event_outbox    unpubl 0   publ 103   attempts 1
b4.outbox_events   unpubl 0   publ   2   attempts 1
DOMAIN_EVENTS messages=134      B4_DLQ messages=0      ROSTER_IMPORT messages=2
```

- 计数**互相对上**：`134 = 31 (B2) + 103 (B3)`，而 B4 的 2 条走 `ROSTER_IMPORT`。
- 该工具自己写明"'attempts 为 0 且旁边有未发布积压'意味着从未有人尝试发布，正数意味着发布者在运行"——
  本读数 `attempts=1` 且**未发布为 0** ⇒ **中继确实在运行并发布成功**；**零死信**。

**(c) 消掉探针明确留下的一个歧义。** 宿主探针报 B3 的统计消费 `idle, no event has ever been delivered
(this is also what a missing consumer looks like)`——它无法区分"缺消费者"与"确实没东西可消费"。
按事件类型数一下就能区分：

```text
b2.outbox by type:  evaluation.progress 12 | submission.accepted 8 | evaluation.failed 8 | evaluation.started 3
                    => evaluation.completed = 0
```

B3 的统计**只**消费 `evaluation.completed`，而此类事件**为 0**（8 次运行全部 `failed`）⇒
`b3.event_inbox recv 0` 是**被解释的**，不是缺消费者。这是"**从未有过一次完成的评测**"这一环境事实的又一处投影，
也是第 (5) 项"真实完成态端到端演示"在本平台达不到的直接原因。

**(d) 第 (5) 项的 provider 面：评语轨已经是真实 DeepSeek。** `b2.evaluation_commentaries` 的两条 `ready` 行：

```text
provider=deepseek  model=deepseek-flash  prompt_version=b2-commentary/1
input_scope = 22 条字段路径的**正向白名单**（不含任何源码字段）
延迟: 09:16:26.559 -> 09:16:36.871 (10.3 s)   09:17:10.365 -> 09:17:16.101 (5.7 s)
text_len 427 / 396        advisory_score = NULL
```

即"真实 provider → 落库 → provenance 可核"这一段**已完成**；`input_scope` 是正向白名单，
这正是"模型看不到源码"这条纪律的**可核形式**。B3 出题侧的同类 provenance
（`deepseek · deepseek-flash · b3-authoring-v1`）已在 §3.31 验过。


---

### 3.36 最终集成序列前的环境盘点（附我自己两个**被证伪**的假设）

**在跑什么**（`Win32_Process` + `ParentProcessId`，`19:37` 读数）：

| 角色 | 进程链 | 启动 | 代码新鲜度 |
|---|---|---|---|
| composition host | `24604 → 37572`（uvicorn `:18000`） | `19:32:13` | **新**（本轮重启，见 §3.34） |
| B2 评测 worker | `31640 → 32276`（`-m autograder_b2.worker`） | `17:47:13` | **旧**——B2 源码此后被改过 |
| B3 消费进程 | `33584 → 12600`（`-m autograder_b3.consumer_cli`） | `17:47:13` | **旧** |
| B4 事件中继 | 无长驻进程（**设计如此**，见下） | — | 已跑过 |

**两个假设被证据否定，记在这里以免复现：**

1. **"同一个入口有两个进程 ⇒ 重复 worker"——证伪。** 读 `ParentProcessId` 后它们是**父子链**
   （`32276` 的父是 `31640`、`12600` 的父是 `33584`、`37572` 的父是 `24604`）：venv python 再 re-exec
   进另一个解释器跑**同一入口**。**每个逻辑进程各一条链，不存在重复消费**。
2. **"运行手册要三个中继，而 B4 中继没在跑 ⇒ 缺一个"——证伪。** `ops/messaging.py:156` 的 `relay` 是
   "**publish the pending outbox once**"，**没有 loop/interval 参数**，即它**本来就不是长驻服务**；
   `:11` 那句"刻意作为独立于宿主的进程运行"说的是"**不在 compose 里**"，不是"必须常驻"。
   证据一致性：B4 发件箱 2 条**全部已发布**且 `attempts=1` ⇒ **它跑过**（从未跑过会是 `attempts=0`、`unpubl=2`）。
   按运行手册原命令实跑一次：`published=0 failed=0 still_pending=0`。

**事件链判据已满足——用的是运行手册自己的判据**（`integration.md:178-180`）：所有 outbox `unpubl=0`、
`attempts=1`、死信 `0`。该节同时写明"`b3.event_inbox` 仍会是 0——它需要一次真实完成的评测"，而
§3.35(c) 把这条从"需要沙箱"**进一步坐实为"此类事件为 0"**（`evaluation.completed = 0`）。该节还明确警告
"**不得因为发布端全绿就暗示消费端已验证**"——本条据此只声明**发布端**已验证。

**最终序列（待 B2 的实现落地后一次做完）**：杀掉上述三条链 → 重装四个模块
（`uv sync --all-groups --extra modules`，四个一起）→ 起 host + B2 worker + B3 消费（B4 中继按需一次性跑）
→ 再验：`probe_host_assembly.py`（restart-ready）、16 条门禁、`event_chain_status.py`、以及**重启之后**的
真实端口 E2E。**顺序不可颠倒**，否则跑出来的就是 §3.33/§3.34 那个"关于旧代码的结论"。

**当前门禁 `14/16`，两条红都是预期中的红**：`check_module_installations`（`autograder_b2` 的
`grades/export.py`、`grades/query.py` 落后于工作树——B2 正在修那两条分页用例）与 `check_test_evidence`
（B2 的记录是一次**真实失败**）。两条由**同一个未完成事项**引起，处置是**不重装、不放宽**。
顺带：`check_module_installations` 的提示文本本轮**就地得到验证**——它完整输出了
`uv sync --all-groups --extra modules --reinstall-package autograder-b2` 与那段后果说明（§3.34 第 2 条裁决）。


---

### 3.37 第 (5) 项三个具名创新的落点核查；并纠正一条**我自己推翻的设计决定**

**(a) 先纠错：`docs/innovation/inventory.md` 的 A6 格原本写着 `scripts/test_evidence.py`"刻意**不并入**
`checks.py`（否则每次契约变更都要重跑五个套件），它是**交付期**门禁"。我在 §3.32 把它并入了，
于是那句话变成了**假的**——这正是我这一整轮在修的缺陷类型，只是这次假话是我自己造成的。**

原作者的**成本判断是对的**，实测一次交付扫描 `B2 368.7 s + B4 139.2 s + B3 50.5 s + B5 5.8 s`。
但它**换来什么被低估了**：从 `0.7.0` 到 `0.16.0`，`--check` 一直报"B1 记录在 `0.7.0`、`B2–B5` 无记录"，
而**没有任何地方执行它**，于是文档引用的 `133` 与实测 `263` 差了 **9 个契约版本**没人发现。
已在 A6 格内就地写清：裁决、代价、以及"代价改为契约变更后必须重跑扫描——这恰是希望被看见的那个代价"。
**推翻一个记录在案的决定，必须把原决定权衡过什么也一并留下**，否则下一个读者只会看到结论变了。

**(b) 第 (5) 项的三个具名创新，逐一到落点核对：**

| 目标里的名字 | 落点（已核对的代码/界面） | 先前读数 |
|---|---|---|
| **题库覆盖度** | B3 的覆盖度端点 + B1 覆盖度页 | §3.31：`total=102 tagged=4 untagged=98`，标签 `[('file-systems',3),('virtual-memory',3),('unix',1)]` 且**重叠已证**（`sum=7 > 4`） |
| **证据可视化** | 质量热力图页的**三态**安全结论（发现/未发现/**未检查**）+ 逐用例证据下钻 + 证据包面板 | §3.31：`attempt_count=6`、`unmeasured_runs=6`、安全 `0/2/4` **三态分开**。证据包面板**环境阻塞**（无报告 ⇒ 不渲染，`404` detail 诚实） |
| **题型配置器** | B1 `features/authoring/`：`AuthoringPage.tsx` + `draft.ts` + `CaseEditor.tsx` | 本轮核对：`adapter_kind`（stdin/file/callable）、适配器 `args`/`stdin`/`expect_exit_code`/`callable`、**比较策略配置器**（`comparison.stdout`/`stderr` 策略 + numeric tolerance）、保存前校验（`authoring.issue.*`） |

**这个配置器里有两处必须点名的诚实设计**（它们正是"出口缺失"那类缺陷的对立面）：
1. `adapter_kind === 'file'` 的已存在版本**被拒绝保存**（`draft.ts:341`，界面 `adapter-file-locked`）——
   因为 `adapter` 是必填字段却**读不回来**，重新声明会**静默丢掉** `files[]`。它选择**拒绝**而不是悄悄改写。
2. `adapter-not-readable` 横幅（`:371`）把上面这件事**明确写在教师眼前**："本页保存时是**重新声明**整个
   manifest，而不是继承已存的那一份"。这就是已登记的 CHG-0025 缺口（adapter 读出口）**在界面上被披露**
   而不是被隐藏——把缺口写出来，比让教师以为"保存等于更新"要好。

**(c) 由此得到一条对第 (5) 项的整体判断**：三个具名创新**都有真实落点**，其中两个（覆盖度、质量热力图）
已被我在真实浏览器/真实端点上验过，第三个（题型配置器）的代码与交互落点本轮核对完毕。
**仍然缺的只有"证据包面板的真实渲染"**，而它被"库里从未有过一次完成的评测"挡住（§3.35(c)），
是环境事实而非实现缺口。

**(d) 重启后的两个"可演示面"读数**（补 §3.34 那次重启）：

- **SPA 完好**：`GET /app` → `200`，含 `id="root"`，`<title>AutoGrader 教学评测平台</title>`；
  `/app/`、`/app/index.html` 同；首屏 JS 包 `/app/assets/index-CBI1o-qO.js` → `200`、**583,759 B**。
  即重启没有破坏静态挂载（它是本轮唯一"重启后必须重验"的东西——§3.33 的教训）。
- **B5 一致性端点**（A2 的"运行时那一半"）：`GET /api/v1/platform/conformance` → `200`，
  `{"consistent":true,"loaded_modules":["B4","B3","B2","B5"],"contract_path_count":47,"differences":[]}`。
  它与 `probe_host_assembly.py` 的读数（47 条声明路径、声明未服务 **0**、服务未声明 **0**）
  **由两条互相独立的机制给出同一答案**。

顺带一条**工具体征，差点被读成宿主故障**：首次用不带 `-UseBasicParsing` 的 `Invoke-WebRequest` 取 `/app`，
报的是 `Windows PowerShell is in NonInteractive mode. Read and Prompt functionality is not available.`——
那是 PowerShell 解析器的问题，与宿主无关。**"命令失败"与"被测对象失败"必须分开读**，
否则会写出一条关于 SPA 的假发现。


---

### 3.38 第 (3) 项最难的那条读数：真实端口上的 `disclaimers`；并记两次**我自己造成的假阴性**

**(a) B2 记录过一条"不可能取到"的读数，重启把它变成了可能。** `B2/docs/phases/active/phase-06-ai-advisory-score.md`
写着：*"运行中的 host 进程早于本批构建（其 `/commentary` 不带 `advisory_score`）"*——正是 §3.33/§3.34 的进程年龄问题。
`19:32:13` 重启后，同一请求给出：

```text
GET /api/v1/evaluation-runs/733610ca…/commentary -> 200  status=ready  advisory_score=null  disclaimers=3
[1] 本内容由模型生成，是非确定性的，不参与判分，也不作为申诉依据。
[2] 分数与逐项证据与本评语无关：删除或重新生成本评语不会改变任何分数。
[3] 参考分是模型的顾问性建议，既不是成绩也不是评分依据；满分由系统按证据填入，不由模型生成。
```

即 `advisory_score` 是**子对象**（契约要求的那条访问路径），`disclaimers` **三条恒在**且第三条**逐字覆盖
分数参考**，而这一切在 `advisory_score` **为 null** 时**同样成立**——恰是契约点名的最难情形（原话：
"A client that renders `advisory_score` without them is presenting a model's estimate as a score"）。
**第 (3) 项的"显式标注"至此在真实端口上验到。**

**(b) 同一件事我错了两遍，两遍都记下来。** 第一次用英文关键词 `-match 'advisory score'` 去比**中文**文案 → 假阴性；
第二次改用中文关键词仍为 False，而我当时的第一反应是"文案不对"。真正的原因是**解码**：`Invoke-RestMethod`
在响应 `Content-Type` 没带 `charset` 时按 **Latin-1** 解 JSON，**内存里的字符串本身就是坏的**（`æ¬å®¹` 这种形态），
`[Console]::OutputEncoding` 改不了它。改用 `WebClient.DownloadData` 取**原始字节** + `UTF8.GetString` 后，
三条文案完整可读、四个 `-match` 全部 `True`。

**这条比它测的东西更值得记**：一个错误的**测量工具**会生产出"契约违规"这种级别的假发现，而且它看上去完全正常。
规则：**对含非 ASCII 的响应做断言之前，先确认自己拿到的字符串是按 UTF-8 解出来的**——用字节 + 显式解码，
不要相信 `Invoke-RestMethod` 的默认行为。

**(c) 顺带记 B2 在飞的临时文件与它的诊断。** `B2/tests/test_zzz_pagination_before_demo.py`（`19:38:43` 创建，
开头自称"**临时演示（跑完即删，不进交付）**"）里的根因判断与我的判断一致、且更精确：

> 旧的断言是"返回顺序 == 插入顺序"。产品的定序是 `(created_at ASC, submission_id ASC)`，而 `timestamptz(3)`
> 会把前三行压成同一毫秒（并列键）⇒ 相对顺序由**随机 UUID** 决定，插入顺序只在其恰好升序（≈1/6）时成立。

即**是测试的期望写错了**（把"返回顺序"当成"插入顺序"），**不是产品缺陷**——所以正确的修法是改那条**假期望**，
而不是放宽断言。

**收工后的核实（`19:45:34` 起 `Test-Path` 为 False）：该临时文件已被删除**，与我上一轮的承诺一致。

**并且我按代码核对了修法本身**（不只核对"文件删了"）：

| 核对项 | 读数 |
|---|---|
| 产品定序是否被改动 | **没有**。`grades/query.py:431` 仍是 `.order_by(submissions.c.created_at.asc(), submissions.c.submission_id.asc())`，`:394` 新增的是"为什么用 `submission_id` 兜底"的**理由** |
| 期望值怎么改的 | `_expected_order` 从"插入顺序"改为**产品声明的全序**：把 `(submission_id, created_at)` 读回来**按声明规则排序**，并在 docstring 里写明"**插入顺序从来不是承诺**" |
| 是否只是放宽 | **不是，反而更严**。新增**前置断言** `len({_ms_floor(r.submitted_at) for r in whole.items[:3]}) == 1`（保证前三条**确实并列** ⇒ tie-breaker 真的被走到）、直接断言并列组内 `submission_id` 升序，并给翻页循环加上限 10——"游标一旦退化（例如按毫秒截断）就变成一条**失败断言**，而不是挂死整个测试进程" |
| 另一条是否产品缺陷 | **是**。`grades/export.py` 新增按毫秒截断的辅助函数（`:315-322`），理由写明：列是 `timestamptz(3)`，导出值必须与 API 值一致；`:328` 另记"用 `Decimal(str(...))` 而不是 `%f`/`round`，后者会引入二进制浮点的近似尾巴"。这条是**改产品代码**，符合我给它的授权 |

**尚未核实的是"现在绿了"**：那需要真跑一次套件，而 B2 的实现方可能仍在跑自己的 pytest——
**两个 pytest 进程同时打同一个测试库（55433）会互相 `TRUNCATE` 对方的 fixture**，从而生产出假失败。
所以我**不在它运行期间抢跑**，这条留给最终集成序列里那次统一运行。


---

### 3.39 第 (2) 项**四个**端点在重启后的宿主上逐个复验

第 (2) 项说的是"**四个**已生效但零实现的契约端点"。逐个点名并复核（此前我逐页验过三页，
但四个**端点**里"复核隔离区"从未在真实端口上打过）。**路径以契约原文为准**：
隔离区在 `/api/v1/intake/quarantines`（不是 `intake-quarantines`），而质量热力图在
`/api/v1/evaluation-runs/quality-summary`（**不是** `question-versions/…`）。

| # | 端点 | 读数 |
|---|---|---|
| 1 | `GET /api/v1/intake/quarantines` | `200` `{"items":[],"next_cursor":null}` —— **空列表而非错误** |
| 2 | `POST /api/v1/intake/quarantines/{id}/resolve`（body `{student_id}`） | 未知 id → `404 "The quarantined intake record does not exist."` |
| 3 | `GET /api/v1/evaluation-runs/{id}/appeal-pack` | 未知 run → `404 "The evaluation run does not exist."` |
| 4 | `GET /api/v1/evaluation-runs/quality-summary?question_version_id=…` | `200`，**草稿与已发布都接受**；键 = `attempt_count`、`failure_categories`、`static_rule_hits`、`case_failure_rates`、`unmeasured_runs`、`security_found`、`security_not_found`、`security_not_checked` |

**第 4 条顺带证实了 C6"证据可视化"的落点**：三态安全结论是**结构上分开的三个字段**，而不是一个布尔，
所以"未发现"与"未检查"在**响应形状层面**就不可能被混为一谈。

两个读数需要**如实标注口径**：
- 这两个版本的 `attempt_count` 都是 **0**（没有作答），所以所有计数为 0、`failure_categories` 为空；
  规则表则是**完整枚举**的（`STAT-PY-*`/`STAT-SH-*` 各自带 `severity`）。**有数据的**热力图读数是 §3.31 那一份
  （`attempt_count=6`、`unmeasured_runs=6`、安全 `0/2/4`）——两者不矛盾，是**不同版本**。
- `POST …/resolve` 只验到"未知 id → `404`"。它的 `422`（"内容类拒绝不可归属"，`CHG-0021` §2.1）分支
  **无法在真实库上触发**，因为 `b2.intake_quarantines` 是空的、且没有写入路径（邮件通道未配置）。
  这一条由 B2 的测试覆盖，**不是**本轮的真实读数。

**又是一次我自己的疏忽，记一行**：第一次打第 4 个端点时得到 `422 "question_version_id is required"`——
**端点是好的，是我没读它的参数就发了请求**。这是本项目里同一类疏忽的第 4 次
（前三次见 §3.32、§3.34、§3.38），所以它值得被写成一条纪律而不是一次偶然：
**"对面报错"之前先确认"我发的东西符合它声明的输入形状"。**


---

### 3.40 第 (3) 项"**学生可见**"用真实学生令牌验到（含负例）

先前只验到**服务端返回**该字段与 B1 的组件测试。这一步换成**真实学生令牌**。
两条带评语的运行都属于 `s.li`（李小明，`fd29cd1d…`），课程 `8cf8259c…`：

```text
s.li 读自己的运行   733610ca… -> 200  status=ready  advisory_score 键存在  disclaimers=3
s.li 读另一个学生的运行 584189c1… -> 403 "The caller is not permitted to perform this operation."
```

- **正面**：学生**确实**能在自己的运行上读到评语与参考分字段（`advisory_score` 键在响应里），
  且三条免责标注一并返回。契约写的是"**本人** / 课程作用域内的教师与助教 / 管理员可读"——
  **"本人"那一支**至此是**真实读数**，而不只是组件测试。
- **负面**：跨学生读取是 `403` 而**不是 `404`**，与 `B2/docs/api/contract.md` 及 B2 的 T-CMT-010 一致，
  即"无权限时**不用 `404` 掩盖**"。**可见与隔离是同一枚硬币的两面，本轮两条都验到。**

**口径必须写清**：这两条运行的 `state` 是 `failed`（沙箱不可用），`advisory_score` 为 **`null`**（无证据 ⇒ 无满分可填）。
所以本轮验到的是"**字段与标注对学生可见**"，**不是**"学生看到一个**非空**参考分"——后者需要一次有证据的运行（§3.35(a)）。


---

### 3.41 运行中的宿主主动说出的一件事：`evaluation-chain: ok - 报告了 N 次未预期失败`

**来源是健康响应本身，不是我构造的检查。** `/health/ready` 里 B2 的 detail 写着
（中文被控制台按 Latin-1 渲染坏了，用 §3.38(b) 的办法解码后）：

```text
evaluation-chain: ok - 评测/成绩端点报告了 N 次未预期失败
```

模块仍报 `healthy: true`。追下去是**三件彼此独立的事**，必须分开说：

| # | 事实 | 依据 |
|---|---|---|
| 1 | **注释与行为不一致**：6 处调用点的注释都写"\`record_failure()\` 计数（**健康检查据此报 unavailable**）"，但 `runtime.py:303-304` 在计数非零时返回的是 **`HEALTH_OK`**；`intake/runtime.py:95-96` 同理（返回 `True`）。**两者必有一处是错的** | 6 处注释 vs 1 处返回 |
| 2 | **计数器从进程启动起只增不减，永不重置** | `runtime.py:146` `_failures: int = field(default=0, init=False)`，`:161` 只做 `+= 1`。因此 detail **永远回不到**那句干净文案；原因早已消失也照样报 |
| 3 | **被计入的集合里包含一个"预期"状态**：沙箱不可用在这台机器上是**被文档化**的条件，而它的 `503` 也走 `runtime.record_failure()` | `api/reference_routes.py:110-113`（该分支注释自己写着"依赖不可用（**含沙箱不可用**）"） |

**受控实验（证明第 3 条，而不是只读代码）**——只增内存计数器，**不写任何存储**：

```text
BEFORE: failures=1
call 1 -> HTTP 503（沙箱不可用）   AFTER call 1: failures=2
call 2 -> HTTP 503（沙箱不可用）   AFTER call 2: failures=3
```

即：**在本平台上，"未预期失败"这个词对触发它的那些事件是不成立的**——它们是预期中的。后果是这条
readiness 细节成了**不可行动且永不干净**的信号，而真正的就绪判定（`HEALTH_OK`）与那 6 处注释相互矛盾。
姑且不论该返回哪个状态，**"永久累计 + 把已知不可用算作未预期"这两点合起来，会让人学会忽略它**——
正是本项目反复记下的"会乱叫的门禁会被关掉"的同族。

**必须如实披露我自己的副作用**：上面那次实验的 **`2` 与 `3` 是我造成的**（两次故意的 `503` 探测），
`BEFORE` 的那个 `1` 在本次进程生命周期内**无法回溯归因**（可能是更早的同类 503，也可能别的）。
所以此后任何人读到"3 次未预期失败"都应当知道：**其中两次是 MAIN 的探测，不是故障**。

**处置**：这属 B2 归属的代码（`runtime.py` / `intake/runtime.py` / 6 处注释 / 计数口径）。
B2 的实现方**此刻正在跑它自己的验证**，我不会中途用消息打断它——**这条登记下来，等它报完再发**，
以免把一次即将完成的复跑搅乱。可选的正确修法有三种（改注释、不把已知不可用计入、或让计数可衰减/重置），
**由 B2 决定哪一种**，我只提供证据。


---

### 3.42 既有失败清零（`4 failed` → `0 failed`）：四条**全是测试缺陷**，我的初判被证伪

B2 写出 `phase-08-known-failure-remediation.md`。**它纠正了我的初判**，而纠正的方式是正确的：
我在授权时写"我**怀疑** keyset 分页那对属于产品缺陷"，它的答复是

> MAIN 的初判是"并列排序键下没有确定性 tie-breaker ⇒ 分页会漏/重"。**核实结果：不成立**，
> 证据是产品代码本身（`grades/query.py::_statement`，两处同源）

并给出定序与游标谓词**逐项同源**的代码。**我的怀疑是错的，它用代码而非措辞纠正了我。**

| 测试 | 根因 | 类别 |
|---|---|---|
| `test_xlsx_timestamp_truncates_like_the_api_not_rounds` | 期望值编码了**迁移 0012 之前**的数据模型 | 测试 |
| `test_cursor_pagination_is_complete_and_disjoint` | 按**插入顺序**断言，而产品按 `(created_at, submission_id)` 定序 | 测试 |
| `test_pagination_is_keyset_not_offset` | 同根因：删的是"插入的第一行"而非"第一页那一行" | 测试 |
| `test_every_timestamp_column_has_millisecond_precision` | `from alembic.versions import …` 是**错的路径假设** | 测试 |

**产品代码只改了两处过时的 docstring（行为一行未动）**——与我按代码核对的结果一致（`query.py:431` 的定序未变）。

**我独立复现了最关键的那条因果**（"为什么 `.124` 对、`.123` 错"）：`timestamptz(3)` 在**写入时**取整，
不是"对外格式化时截断"：

```sql
CREATE TEMP TABLE t (x timestamptz(3));   -- 临时表，不改任何持久数据
INSERT INTO t VALUES (timestamptz '2026-09-15 08:00:00.123999+00'),
                     (timestamptz '2026-09-15 08:00:00.123499+00'),
                     (timestamptz '2026-09-15 08:00:00.123500+00');
-- stored: .123000 / .124000 / .124000
```

即 `.123999` 存进去就是 `.124`，此后**任何一层都再也看不到** `.123999`。旧期望成立的前提
（"列保留微秒、对外格式化时截断"）在 `0012` 之后已不存在。**修法由我自己复现的事实支撑，而不只是它的说明。**

**两处判断值得单独记下来**：
1. **`alembic.versions` 没有用最直接的那条路。** 根因是路径假设：`B2/alembic/` 是 Alembic 的**脚本目录**
   （无 `__init__.py`），而 site-packages 里的 `alembic` 是**常规包**；按 PEP 420，常规包优先于命名空间目录，
   于是那个导入**永远**解析不到本仓库。给脚本目录加 `__init__.py` 看似最直接，**但会让 `import alembic`
   在测试进程里遮蔽真正的 Alembic**，迁移文件里的 `from alembic import op` 立刻失效——
   那是**把一个测试缺陷升级成产品缺陷**。它改成了按文件路径加载该迁移模块。
2. **分页那对的"12 次连跑 12 passed"是统计证据，它自己标注了这一点**：若并列键上没有确定性 tie-breaker，
   连过 12 次的概率是 `(1/6)^12 ≈ 4.6e-10`；形式化部分另由新断言
   （并列组内 `submission_id` 升序）直接钉住。**没有把统计证据说成证明。**

**读数归属必须分清**：`ruff` 干净、`1298 passed, 9 skipped, 2 xfailed in 386.22s [exit 0]`（收集 1309）
是**在 B2 自己的 venv + 测试实例 55433 上**由它取得的读数，**不是我测的**。本批按我的要求
**没有碰 MAIN 的 venv、没有重写 `B2.json`**，所以"test evidence"这条门禁要等我重录之后才会看到绿色读数。

**下一步（最终集成序列）**：等 B2 的实现方停止 → 重装四个模块 → 重启 host/worker/消费 →
**重启之后**重录 B2 的证据与做真实端口 E2E。**顺序不可颠倒**（§3.33/§3.34）。


---

### 3.43 把"服务进程是否加载了当前代码"落成一个可复用的前置检查器

§3.33/§3.34 证明了这个缺口（门禁全绿 + `restart-ready` + **线上仍是旧行为**），并判定它**不能做成
`checks.py` 门禁**——门禁必须在没有宿主运行时也能通过。本轮把它落地为 `.runtime/host_freshness.py`，
三条路径都实测过（**包括"stale"那一支**，用 `--site-packages` 指向一个 mtime 在未来两小时的合成目录，
即受控输入而非伪造结果）：

| 输入 | 读数 | 退出码 |
|---|---|---|
| 运行中的 `18000` | `pid 37572 started 19:32:13+08:00`；最新安装模块文件 `19:20:05+08:00`；`+728s` | **0 fresh** |
| 空闲端口 `18099` | `cannot determine freshness: nothing is listening`，并列出每条失败原因 | **2** —— fail-closed，**绝不把"说不清"当绿色** |
| 合成 site-packages（mtime 在未来） | `-8396s` + `STALE: … Restart the process first.` | **1 stale** |

**写这个脚本时我自己踩了三个坑，全部记下来**——它们的共同点是**报告一个与事实相反的状态**：

1. **`except OSError: continue` 吞掉了真因**：它声称"18000 上没有监听"，而宿主明明是活的。
   这与"红了却说不清为什么的门禁"是同一类缺陷。改成把每条失败原因**带出来打印**后立刻看到
   `pwsh: FileNotFoundError`——**`pwsh` 不在此环境的 PATH 上**，真正的 shell 是 `powershell.exe`。
2. **多行命令里的行尾 `|` 在 `-Command` 下会吞掉其余脚本**：表现仍是"没有输出"，而调用方读成"没有监听"。
   改成**单行**查询。
3. **`ConvertTo-Json` 默认美化输出多行**，而我只解析最后一行（`}`）：又一次"解析失败"被读成"没有监听"。
   加 `-Compress`、整体解析、失败才回退到最后一行。

**三次都表现为同一个假结论，而三次的真因完全不同。** 这是 §3.38(b) 那条纪律的第二次现身：
**测量工具的错误会生产出关于被测对象的假发现，而且它看起来完全正常。**

**已知范围限制（写在脚本 docstring 里）**：`autograder_host` 是 editable 安装，它加载的就是工作树，
没有可比对的安装副本；因此本检查覆盖的是**四个模块 wheel**——那才是会与工作树分叉的副本。


---

### 3.44 我建的门禁把一个**无意义的绿色读数**写进了台账：交付扫描没给套件它需要的数据库

**发现方式**：最终集成序列里重录 B2 证据，读数是

```text
  632 passed, 677 skipped in  32.00s     <- 我这套交付扫描得到的
 1298 passed,   9 skipped in 386.22s     <- B2 自己的 runbook 得到的（同一契约、同一份代码）
```

两者都 `exit=0`。**门禁只看 `exit_code`，于是会转绿**——这正是它存在的理由（"文档引用的数字必须
是当前契约下的实测值"）被自己的实现绕过的情形。

**根因**在 `B2/tests/conftest.py:31-36`：`ADMIN_URL = os.getenv("B2_TEST_DATABASE_URL") or
os.getenv("DATABASE_URL")`，两者都没有时**所有数据库用例 `skip` 并说明原因**。而 `B2/.env` **不存在**，
于是 DSN 只能来自**外层 shell 的偶然环境**：

| 扫描时刻 | 环境里有 `DATABASE_URL`？ | 读数 |
|---|---|---|
| `11:28` | 有 | `1293 passed, 9 skipped` |
| `19:54` | 没有 | `632 passed, 677 skipped` |

**同一契约、同一条命令、两次相差 666 条用例，而记录里没有任何字段能让人看出来。** 更糟的是
`run_suite` 的 docstring 当时写着 "the way its own runbook does"——**它没有提供 runbook 要求的那个环境**。

**修法：从模块自身的权威派生，不手抄一份 DSN。**

1. `test_database(module)`：读 `B<n>/compose.yaml` 里那个 `postgres` 服务的 `POSTGRES_*` 与宿主端口，
   拼出 DSN。派生结果与模块自己的声明逐条一致：`B2 127.0.0.1:55433/autograder`、`B3 …55434…`、
   `B4 …55435…`、`B1/B5` 为 `None`。
2. `run_suite` 把它作为 `DATABASE_URL` 注入子进程，并把**去掉凭据**的 `host:port/db` 写进证据物件的
   新字段 `database`。
3. 门禁新增判据：**模块声明了 test 实例、而记录里没有 `database` ⇒ 失败**，理由写明"该运行描述的是
   一个**子集**，其总数不是模块的总数"。

**注入是安全的——这一点我逐模块核实过**（否则会把破坏性套件指向共享集成库）：

| 模块 | conftest 怎么取库 | 注入的影响 |
|---|---|---|
| B2 | 只读 `B2_TEST_DATABASE_URL`/`DATABASE_URL`，无默认 | **必须**注入，否则静默跳过 |
| B3 | 用 `B3_TEST_*`，**默认即 55434**，且 `conftest.py:81` 自己设置 `DATABASE_URL` | 我的值无关（被它自己覆盖） |
| B4 | 导入时把 `DATABASE_URL` 钉到 `B4_TEST_DATABASE_URL` 或其默认 **55435** | 我的值被覆盖 |
| B1 / B5 | B1 是 Node；B5 的 conftest 自己加载 `.env.integration` | 不需要，`test_database` 返回 `None`，门禁不要求 |

**验收判据**：重录 B2 后其读数应回到约 `1298 passed / 9 skipped`——那是 B2 自己 runbook 的数字。

**这条的教训与 §3.41 同类但更重**：§3.41 是**运行时的信号**说了不成立的话；这条是**我自己的记录机制**
说了不成立的话。一个只在 `exit_code` 上判定的门禁，对"跑了但只跑了一部分"是盲的——
**"绿色"必须包含"跑的是全部"**，否则台账里的绿与文档里的手抄数字一样不可信。

**验收（真实读数）——修法的判据在改之前就写好了，不是事后凑的：**

```text
B2 re-record -> exit=0  {'passed': 1298, 'skipped': 9, 'xfailed': 2, 'duration_ms': 411350}
                         （= B2 自己 runbook 的数字，逐项一致）
B3 re-record -> exit=0  {'passed': 393,  'duration_ms': 47980}     （不变：其 conftest 本就自足）
B4 re-record -> exit=0  {'passed': 227,  'duration_ms': 144440}    （不变）
gate_status  -> 16/16 gates pass
python -m autograder_host.checks -> exit 0
```

**目标第 (1) 项的"修复 MAIN 红门禁"至此达成：`16/16`，且文档化的四条通过判据逐字满足。**

顺带修掉一处**我自己的不一致**：`checks.py` 打印的第四条判据是
`test evidence matches the contract the repository is on: ok`，而我在 `AGENTS.md` 的"通过判据（四条输出）"
里写的是 `test evidence is current and green for every module at this contract: ok`。
**文档化的判据与实际输出不一致**，而那段文字正是人拿来逐字比对的（第四条本来就比"一致"更强：
它还要求那次运行是通过的）。已把 `checks.py` 的打印改成与 `AGENTS.md` 一致，并**用程序核对四条全部逐字命中**。


---

### 3.45 "中继真的在发布"用**行为**证明；并纠正两处把 `attempts` 读反的**我自己的**文字

**(a) 为什么计数器的值不能当证据。** 重启后我读到 `unpubl=0 / attempts=1`——但那是**历史行**上的计数器，
它**不能**证明**新进程**会发布。所以做一次行为实验：跑 `.runtime/e2e_submission.py`（真实学生令牌、真实端口）。
**执行前**：`b2.outbox_events` 31 行、已发布 31、`max(insert_seq)=31`。

```text
POST /submissions -> 202  state=validated
[20:10:11] state=validated     [20:10:15] state=failed  run=838be269-7d0b-4137-a6e3-2c8412b92280
执行后: total 38  published 38  unpublished 0
新行 32..38: submission.accepted | evaluation.started | 4× evaluation.progress | evaluation.failed
             全部已发布，last_error 全空
```

即**一次提交 → 7 条新事件 → 全部被实时发布**，`unpubl` 回到 0。**"三个中继与 worker 已启动并在工作"
至此是行为读数，而不是对计数器含义的推断。**顺带同一次实验也验到：`GET /questions` `200`、
提交 `202`、状态机 `validated → failed`（Windows 无沙箱，`ADR-0004` 禁止降级为裸进程）。

**口径**：这次探针向集成库写入 1 条提交 + 1 个 `failed` 运行（`cf7d5e96…` / `838be269…`），
与既有的 8 条同源探针残留一致，**是有意的**。

**(b) 两处我自己的文字把 `attempts` 读反了，数据直接否证了它们**：`attempts=0` 的 23 行**全部已发布**、
`attempts=1` 的 15 行**也全部已发布**、**0 行带 `last_error`**。回到代码查清语义：新建行是 `attempts=0`
（`events/outbox.py:96-99`），**只有** `_record_failure` 会 `+1`（`dispatcher.py:280-300`），
而 `_mark_published` 只置 `published_at` 并清 `last_error`（`:260-274`）——所以
**`attempts` 计的是"失败过"，"首次即成功"的健康行恰恰是 0**。两处都已改：

| 位置 | 原文 | 现文 |
|---|---|---|
| `scripts/event_chain_status.py` | "'attempts' 为正意味着发布者在运行**并且失败**" | 它计的是**失败过**的次数，正数意味着"曾失败过**且后来送达了**"；仍在失败的行出现在 `unpubl`/`dead` |
| `docs/runbooks/integration.md` | 判据"`unpubl` 为 0、**`attempts` 为 1（不是 0）**、死信为 0" | 判据是 **`unpubl=0` 且 `dead=0`**；且这两条**必要而非充分**（长时间没有新事件时同样成立），要证明发布仍可用必须**产生一条事件**并看 `unpubl` 回到 0 |

**这条与 §3.41、§3.44 同族**：§3.41 是**运行时的信号**说了不成立的话，§3.44 是**我的记录机制**说了不成立的话，
这条是**我的解释文字**说了不成立的话——三次都是靠"回到数据/代码"纠正，三次都表现为**看起来正常**。

**顺带一条观察（未改，不属本轮范围）**：`ruff check scripts/event_chain_status.py` 报 3 条**既有**问题
（`sys` 未使用；两处闭包捕获循环变量 `target`，B023）。它们在我改动之前就存在，且当前调用点紧随定义、
因此不发作，但那是脆弱的写法。记在此处，**不顺手改无关代码**。


---

### 3.46 第 (5) 项的"真实 DeepSeek provider 端到端"在**重启后的宿主**上重验：`ALL PASS`

此前的读数来自较早轮次；本轮在 `20:12` 那个**重启后且被 `host_freshness.py` 判定为 fresh** 的宿主上，
重跑 `.runtime/e2e_authoring_provider.py`（RQ-T-10 出题辅助，真实 provider）：**八项断言全过**。

| 断言 | 读数 |
|---|---|
| 空 `topics` 必须被拒（枚举面就是边界） | `422` |
| 真实 provider 往返 | `200`，**9.3 s** —— `>= 1 s` 本身就是读数：桩是毫秒级 |
| provenance | `{"provider":"deepseek","model":"deepseek-flash","prompt_version":"b3-authoring-v1"}` |
| 建议数与形状 | 8 条，**全部 `confidence='advisory'`**，每条都有 topic 与非空 advice |
| ADR-0009 决策 5 的显式标注 | 3 条 disclaimer |
| **ADR-0009 决策 3 的**结构性**断言** | 响应里**没有任何 key** 指向受保护内容（`offending key paths=[]`，31 个 key path 全量列出） |
| "征求意见不是编辑" | 提问后草稿**未变** |

**为什么那条结构性断言比"返回 200"重要**：契约承诺"建议不得复述参考答案/隐藏用例/期望输出"——
**承诺不可测，而"缺少那个字段"可测**。脚本查的是**响应里不存在这样的键**，因此它是一条**能红**的断言，
而不是一句声明。

**同时如实记下它演示不了什么**（脚本 docstring 自己写明，与 §3.35(a) 一致）：`advisory_score` 需要
`case_evidence`，而本平台没有（无 Linux 沙箱 ⇒ 从未有过 `completed` 运行），所以**真实的非空参考分在
这台机器上无法产生**。能演示的 AI 面是**出题辅助**（只需一道题），这正是它被选中的原因。


---

### 3.47 我**亲自**在浏览器里看质量页（不依赖 B1 的报告）：主体成立，但静态规则那段自相矛盾

真实浏览器、真实会话（界面自己显示 `t.zhang · 助教/教师 · 契约 0.16.0`）、填真实版本号查询。**页面一大片
诚实设计都成立**（这些是我自己读到的，不是转述）：

- **去标识化写明是契约强制**："响应只有计数，没有学生 id、没有提交 id、没有源码、没有期望输出。本页因此
  **不提供任何下钻到个人的入口**——它回答的是「这道题有没有区分度」，不是「这个人做了什么」"；
- **样本量放在每一项计数上方**（"先看样本，再看结论"），并把 `attempt_count` 的定义转述出来；
- **`unmeasured_runs` 单独成列**且理由写明："「没测量到资源」与「检查过、没发现问题」是两件不同的事，
  合计起来会同时丢掉两个意思"；
- **三态安全各自成列**，第三项直写 **"从未检查（不等于安全）"**；
- **失败类别把 `infrastructure_error` 单列**："与题目难度混在一起，会把基础设施故障读成「这题太难」"；
- 两条**阅读辅助被明确标注为辅助**（色带阈值、"门槛 5 是本页的**阅读辅助**，不是契约规则：契约只说
  「很少」，没有给数值"）；
- **空数据时不给结论**："该版本在这个时间范围内没有任何评测运行（`attempt_count = 0`）：没有可读的信号。"

**但静态规则那一段与它自己渲染的数据矛盾**：小标题写 **"静态规则命中（只含命中过的规则）"**，
而表里列了 **15 条 `count = 0` 的规则**；同段说明又写"**契约没有给出规则目录，因此本页不替你列
「从未命中」的清单——那需要一份 B1 拿不到的规则全集**"——而它**正在渲染**一份含 0 命中的 15 条目录。

**三段证据给出结论：**

| 来源 | 说法 |
|---|---|
| 契约 `b2.yaml:827` | "Which static rules **actually fired**, and how often. A rule that never fires on any submission is either useless or mis-specified -- **that is the point of surfacing it**" |
| B2 实现 `evaluation/quality.py:316-333` | `_rule_hits()` 的 docstring：**"以 B2 规则目录逐条为准（未命中即 0）"**，目录之外的 `rule_id` 再追加。**故意返回完整冻结目录**——这正是契约那句 "surfacing it" 的落地 |
| B1 页面 | 小标题与说明宣称"只含命中过的规则／拿不到规则全集"，而它手里就有一份 |

即：**B2 的行为是对的、且是有意的；B1 的标签与说明描述了另一个数据形状**——它把"契约没给目录"写成理由，
而那份目录正是它渲染出来的东西。

**权威与处置**：
1. 契约那句**本身有歧义**（"actually fired" 隐含过滤，紧接着的一句又要"surface the never-firing ones"）。
   措辞应在下一个契约批次收紧为"**冻结目录逐条给出计数，未命中即 0**"。**本轮不改 `b2.yaml`**——
   那会改变摘要并让五份证据同时失效（同 §3.32 的裁决）。
2. B1 的标签与说明属 B1，已发回其归属方：**要么改标签与说明以匹配数据，要么真的过滤**——但过滤会丢掉
   契约明说的那个意图，所以我认为前者与契约意图一致。**我只给证据，不替它决定实现。**


---

### 3.48 另外两页的独立浏览器复核：隔离区、题库覆盖度

**(a) 隔离区页**（`/app/intake/quarantines`）——§3.31 的三条主张**由我亲自逐条确认**，且页面比主张更完整：

- **空列表而非错误** ✓：「隔离记录（已加载 0 条）」+「当前筛选下没有隔离记录。」，并**再加一句诚实提醒**：
  「「未处理」为空**不等于**没有匹配失败的邮件：先确认上面的处理状态筛选。」
- **无"尚未实现"横幅** ✓。
- **`CHG-0021` §2.5 的裁决被渲染在界面上** ✓——而且呈现的是**理由**而不只是结论：
  「`resolved` 本页**始终显式发送**（`?resolved=false` / `?resolved=true`）。契约已把省略它的语义定为
  「**不过滤（全部）**」——**判据是表达力**：参数是 `boolean`，若省略等于「只看未归属」，那么「列出全部」
  将没有任何请求形式可以表达。」
- **而"始终显式发送"这句我用请求验证过**，不是只读文案：
  `GET /api/v1/intake/quarantines?resolved=false&limit=200 -> 200`。

**(b) 题库覆盖度**（`/app/questions`）：

- 「**标签覆盖度**」一节存在，并写明它是**分布而不是名单**：「它是**分布**（只有计数与标签），不是名单：
  契约**刻意不给任何 `question_id`**——带上 id，它就成了逐题翻查题库的**旁路**。」
- 与质量页同一个诚实模式：**`course_id` 是契约声明的必需参数，未选课程时不发任何请求**（"也不替你猜一个作用域"）。
- 「公开题目 DTO 中不存在参考答案、隐藏用例与 expected output；页面在此基础上**再做一次字段校验**后才渲染。」
- 「包含草稿」开关旁注明契约默认：`published_only` 默认为 `true`，"我建的题去哪了"需要一个明确开关而不是靠猜。
- 标签渲染与 §3.31 的读数一致（绝大多数「（无标签）」，少数显示 `virtual-memory file-systems`）。

**(c) 我的一个假设被证伪，顺带确认清单里那条"必修"已修**：线上请求里的 `limit=200` 让我怀疑这是清单 `C2`
（"硬编码 `limit:200` 单页无分页 ⇒ 超 200 行**静默丢数据**"）没说到的**第四处**。读代码后**不成立**：
`useQuarantines.ts:85-87` 用 `getNextPageParam` 跟随 `next_cursor`；而且**全应用六个列表特性都跟随游标**，
其中 `useUsers.ts:23-24` 与 `useGrades.ts:25-27` 还写明了"**曾经**写死 `limit: 200` 且只取第一页…现在跟随
`next_cursor`"。页面上也能看到它的表现：「已加载 100 道」+「加载更多」+「已到末页」。
**即清单 `C2` 那条"必修（不算创新）"已经修掉**，`limit=200` 只是页大小。

**一条跨三页的观察——B1 的同一个诚实模式**：三个页面对"必需参数"的处理完全一致——**契约声明为必需时，
界面不猜、不发请求，并把"为什么不发"写在界面上**。我在 §3.39 用探针打质量端点时得到
`422 question_version_id is required`，而**界面根本不会发出那个请求**。这是"契约的必需性被消费方真正读懂"
的证据，而不是一句文案。**我自己错了两次、界面一次都没错**——这个对比值得记下来。


---

### 3.49 第 (3) 项"教师可见"用真实教师令牌验到，**同一次把边界也验到了**

此前只验了 admin 侧（§3.38）与学生侧（§3.40），教师侧依赖域规则与测试。本轮用真实 `t.zhang` 令牌打两次，
两次**恰好落在边界两侧**：

| 令牌 | 运行所属课程 | 与令牌的关系 | 读数 |
|---|---|---|---|
| `t.zhang` | `8cf8259c-…` | **有课程级 `teacher` / `assistant` 授予** | **`200`**，`status=ready`，`advisory_score` 键存在，`disclaimers=3` |
| `t.zhang` | `371bf950-…` | **无课程级授予** | **`403`** `"The caller is not permitted to perform this operation."` |

角色表印证了机制而不是巧合：`t.zhang` 持有全局 `assistant`+`teacher`，而**课程级授予只在
`8cf8259c`**（`b4.roles` 逐行核对）。因此 `200` 不是"教师能看到一切"，`403` 也不是"缺权限配置"——
**是课程作用域判定在同一次探测里同时给出了两侧。**

**第 (3) 项的"学生/教师可见"至此是一张完整的可见性矩阵**（每一格都是真实令牌 + 真实运行的读数）：

| 主体 | 对象 | 读数 |
|---|---|---|
| 学生 `s.li` | **自己的**运行 | `200`（字段与三条标注都在） |
| 学生 `s.li` | **另一学生的**运行 | `403`（**不是 `404`**：无权限不用 `404` 掩盖） |
| 教师 `t.zhang` | 自己**有课程授予**的课程下的运行 | `200` |
| 教师 `t.zhang` | 自己**无课程授予**的课程下的运行 | `403` |
| `admin` | 任一运行 | `200` |

**仍未执行**：让上述任何一格显示出**非空**的 `advisory_score`——它需要一次有 `case_evidence` 的运行，
而本平台没有（无 Linux 沙箱）。**这条限制在五格里同样成立**，不得因为"字段可见"就写成"参考分已演示"。


---

## 4. 未执行（必须如实记录，不得表述为已通过）

| # | 未执行项 | 原因 |
|---|---|---|
| 1 | `CHG-0018` 的实现侧验证 5 条（参考分进不了报告/成绩/导出、删评语重放分数不变、默认关闭无出网、越界即失败、`max_score` 来自证据） | **已执行**（B2 `tests/test_commentary_track.py` 的 T-ADV-001..011 共 11 条，由交付扫描那次 `1293 passed` 的运行覆盖）。MAIN 另在**真实端口**复验两条：`/commentary` 返回**子对象** `advisory_score`（非平级数字），且 `disclaimers` **三条恒在**、第三条**逐字**覆盖参考分——**在 `advisory_score` 为 null 时同样成立**（§3.38）。**唯一未执行**：真实数据上产生**非空**参考分，因为库里没有任何"有证据的运行"（§3.35(a)） |
| 2 | `CHG-0011`…`0017` 的约 20 条实现侧断言 | 承接 `phase-01 §12.15`，本轮未清 |
| 3 | `ADR-0015` / `CHG-0019` 的参考解答验证编排 | `CHG-0019` 已提交并裁决（目标 `0.15.0`），**实现未开始** |
| 4 | Linux 安全/资源验收、真实 `completed` + 分数路径 | 本环境是 Windows，无 nsjail / cgroup v2 |
| 5 | 浏览器端到端（真实端口、真实 JWT、走 B1 界面） | **已部分执行**：§3.31 由 B1 在真实浏览器里**逐页**验过四处横幅与覆盖度/隔离区/质量热力图三页（含 `CHG-0021` §2.5 的裁决被逐字渲染）；MAIN 本轮在**重启后**的宿主上补验 SPA 挂载（`/app` 200、JS 包 583,759 B）、`platform/conformance`（`consistent:true`、`differences:[]`）与发布工作流四个读数（§3.34、§3.37(d)）。**仍未执行**：证据包面板（前置数据不存在）与需要沙箱的完成态闭环 |
| 6 | `E11` / `RQ-B-3-30` 验收 5（窗口路径 23.8 s 不达标） | 承接 `phase-01`，未达标，不得抹平 |
| 7 | `E16` 的 57 条迁移文本违规 | 库已合规，静态门禁的文本扫描不建模 `ALTER TABLE` |
| 8 | `scripts/test_evidence.py --all` | 需在各模块套件跑完后录制 |
| 9 | ~~host 重启后经真实 HTTP 的取包与完整评测~~ | **已执行**，见 §3.10 |
| 10 | `check_module_installations`（**预期红，非回归**） | B2/B3 的工作包**正在改源码**，安装副本必然与工作树分叉，而该门禁按设计 fail-fast 拒绝"校验另一个 revision"。报错逐字指向 agent 正在写的文件（`commentary/parsing.py`、`api/router.py`、`domain/catalog.py`）。**处置是等工作包结束后重装再跑，不是绕过门禁** |
| 11 | `check_document_encoding_and_links`（**预期红，非回归**） | 同因，且**两个**在制品各有一条：B2 在自己 `docs/requirements/traceability.md` 与 `docs/README.md` 里链了尚未创建的 `phases/active/phase-06-ai-advisory-score.md`；B1 在 `docs/collaboration/interfaces.md` 里链了尚未创建的 `../phases/active/phase-04-question-tag-coverage.md`。都是各工作包的**在制品**，MAIN **不得中途去改另一个 agent 正在写的文件**。凡不落在在制品上的文档门禁均为绿（含 `check_change_records`、`check_no_replacement_characters`）。**注意 B3 已结束且其侧死链已清零** |
| 12 | `CHG-0021` 的 7 条实施侧验证 | 仅裁决并提交，契约文本与实测未落地 |
| 13 | ~~B3 的覆盖度端点在**真实宿主**上的端到端（宿主装载、真实 JWT）~~ | **已执行**，见 §3.15 A：探针两个方向差集均为 0、宿主重启后四模块健康、真实 JWT 取到 `200`、独立 SQL 交叉核对 8/8 一致 |
| 14 | B3 覆盖度在**真实题库**上的读数与规模性能 | **已执行（重叠性质）**，见 §3.18 二：`sum(per-tag)=7 > tagged=4`，端点与独立 SQL 逐项相等（§3.15 A 首次交叉核对）。**仅剩规模性能未测**——`EXPLAIN ANALYZE` 只在 10 行数据下实测（见 `CHG-0020` §5 的更正） |
| 15 | `ix_questions_tags` 是否会被 planner 选中 | 未测。`CHG-0020` §5 的原始断言（"聚合走该 GIN 索引"）**已被实测推翻**，见该文件 §5 |
| 16 | ~~`CHG-0017` 的实现（`cases` 省略即保留）~~ | **已执行**，见 §3.17 A/B 与 §3.18 一：373 → 382 passed；**composition host 上复测通过**（PATCH 省略 `cases`/`reference_answer` → `200`，新旧版本受保护内容摘要**逐字相同**）。顺带在真实栈上确认了"分支不继承 fixture"（`fixtures_changed=true` 而什么都没改） |
| 17 | ~~覆盖度视图在**浏览器**里对着真实宿主渲染~~ | **已执行**，见 §3.26 B：真实浏览器 + 真实 B3，`102/4/98`、`file-systems=3, virtual-memory=3, unix=1`、恒等式与契约顺序全部与独立 SQL 一致。**并且它抓到了一条说假话的常驻横幅**（§3.26 C，已派 B1 修） |
| 18 | ~~`CHG-0022` 的 5 条验证~~ | **已执行**，见 §3.19 B：线上响应通过校验，且**五条反向断言全部被拒**（含 `question_count=0`、41 字符、大写、下划线、空格+大写）＋`maxItems: 20`。**只证明"合法值通过"等于没证明约束存在**，所以反向那一半是分量所在 |
| 18b | `CHG-0024` 的 3 条验证 | **第 1 条已执行且成立**：修前该断言**失败**（`versions/0/published_at: None is not of type 'string'`），修后**通过**——由同一条断言的前后反差证明，不是另写一条。**第 2 条（非法日期字符串仍须被拒）无法按原文执行**：`jsonschema` 的 `format` 默认**只作注解、不做断言**，所以要它成立必须显式启用 format checker。**这是一条关于验证工具的发现，不是一个已通过的验证**——如实记为未执行 |
| 19 | `CHG-0019` 的 6 条验证 | 契约已生效（`0.15.0`，冻结）；**端点未实现**（B2 正在实现，分叉清单里已出现新文件 `api/reference_routes.py`）。`CHG-0023` 的 7 条**已执行并通过**，见 §3.21 A |
| 19b | `CHG-0025` 的 6 条验证（含**往返**断言：把读回的内容原样回发后 `adapter_kind_changed=false`、`size_limits_changed=false`）＋`CHG-0026` 的 4 条 | 两者均为"已采纳，实施排期"，同批 `0.16.0`；`0.15.0` 已冻结，故未落地 |
| 20 | **`adapter` 没有读出口**造成的缺陷面 | **B1 核实后结论更强**：它自己就是**重新构造**的，不是"碰巧"——读出口只有 `adapter_kind`/`max_files`/`max_total_bytes`，而 `adapter` 是写必填之一。因此**打开一道在 UI 之外创建的题目时，保存会用一份不完整的 manifest 覆盖已存的那份**（**数据损坏**，不是"脚本不方便"；B1 的测试全绿，因为前端正好是那个会重新构造的客户端）。**已裁决**：`CHG-0025` 新增"写形状的读形状"（课程写权限，返回 `QuestionDraftInput` 同形**减去 `cases`/`reference_answer`**）；**否决**"让客户端自己缓存"（那会变成每个客户端各自维护的服务端状态影子副本，未缓存过的题目仍静默降级，且没有契约可校验）。**实现排期 `0.16.0`** |
| 21 | **分支版本不继承 fixture** | `as_new_version=true` 的新版本声明了 `adapter.files` 却没有 fixture 字节，`publish` 会在摘要比对处拒绝（§3.17 D-2）。超出 `CHG-0017` 范围，未改，待独立评估 |
| 22 | B1 按 `CHG-0017` 放开"只改标签"的保存 | 边界已告知 B1（**四个公开必填字段仍必须发送**，只有 `cases`/`reference_answer` 可省略，§3.17 B）；其落地与验证未执行 |
| 23 | `CHG-0018` 经**宿主**的 HTTP 端到端 | B2 侧实现已完成（迁移 `0014` 已在集成库、真实 DeepSeek 经生产路径调用过一次），但集成库 `evaluation_results = 0`、`case_evidence = 0`、8 条运行**全部 `failed`** ⇒ 无证据 ⇒ 真实 `advisory_score` 无法产生。**不会伪造一条 completed 运行来制造它** |
| 24 | **宿主当前不可重启**（B2 实现中） | B2 已在自己的 `module.yaml` 声明 `/api/v1/reference-verifications`（因此 `check_route_declarations` 转绿），但**安装副本的描述符还没有**，探针如实报 `host assembly FAILED: RegistryError: B2: … routes: descriptor=[4 项] module.yaml=[5 项]`。**18000 上正在运行的宿主仍 `ready=true`、四模块健康**（它跑的是先前安装的副本）。分叉清单里已出现 B2 的**新文件 `api/reference_routes.py`**，说明端点在实现中。**处置：等 B2 结束后重装再重启**——不在实现中途重装（会装进半成品，且立刻再次分叉）。这与 §3.17 C 是同一形状：**契约/声明版本有第六份副本（安装后的描述符），升版或改路由后必须重装** |
| 25 | ~~真实 provider 的端到端演示（`RQ-T-10` 出题轨，经真实宿主）~~ | **已执行**，见 §3.22：`200` / `9.4s` / `provenance=deepseek·deepseek-flash·b3-authoring-v1` / 8 条建议 / 3 条免责声明 / 递归键遍历无受保护字段 / 题目未变。**这是本环境能做到的最完整的 AI 端到端**（`RQ-T-30` 的参考分需要 `case_evidence`，本环境恒为零，见第 23 行） |
| 26 | ~~`RQ-T-10` 在**浏览器**里走 B1 出题页触发（而非直接调用端点）~~ | **已执行**，见 §3.27 A：真实浏览器 + 真实 DeepSeek，7 条实质建议、`provenance=deepseek·deepseek-flash·b3-authoring-v1`、3 条免责声明原文照录、全部 `confidence='advisory'`。与 §3.22（端点侧）合起来构成目标第 (5) 项的完整界面+端点证据 |
| 27 | `CHG-0027` 的 4 条验证（含**反向**：`reason_code="something_else"` 必须被契约校验器拒绝；以及"查询参数用一个不存在的码筛选，行为须与改动前逐字相同"） | 已采纳，实施排期 `0.16.0`；`0.15.0` 已冻结，故未落地 |
| 28 | B1 的"验证并发布"面板在**真实宿主**上走一遍（B2 的 `CHG-0019` 端点就绪后） | 端点已就绪（探针 `declared but NOT served: none`），但 B2 正在修**自调用死锁**（§3.25 C），因此**尚不可验收**；修好后与 §7.2/7.3/7.4 同批执行 |
| 29 | **`coverage-declared-contract` 横幅改对**（删除或数据驱动） | B1 已派。这是"**界面说了不成立的话**"（§3.26 C），而修法与验证**都必须在真实浏览器里确认**——它的 262 项组件测试用夹具、且该横幅无条件，因此永远抓不到它 |
| 29b | 验证面板的 `503` 结论**不超出证据**（标题中性化，或从 `detail` 读原因） | 已建议 B1（§3.27 C）。**与 29 分开**：29 是假话，这条是"推断过宽且已把原始证据一并呈现"——**处置的严厉程度不同**。B2 修完死锁后 `503` 会重新只意味着"没有沙箱"，故优先级低 |
| 30 | `CHG-0028` 的 6 条验证（草稿也可取包；已发布版本**仍然**做摘要比对；无用例仍 `409`；端到端"验证草稿→存证→发布"） | 已裁决，**B3 正在实现**（契约 `0.16.0` 已落地）。**其中"验证草稿→存证→发布"整链在本平台仍无法完成**：执行参考解答需要 Linux 沙箱 |
| 30b | ~~发布门禁的事实基础（门禁是真的 / 教师不能自证 / 存证被交叉核对）~~ | **已执行**，见 §3.30 A：`ALL PASS`，三条读数全部取自真实端口。**这是死结论证第一次有了运行期依据** |
| 30c | `publish` 拒绝信息里的**过时补救指引**（指向 `MAIN/scripts/verify_question_bank.py`） | 已派 B3（§3.30 B）。**它出现在教师唯一被卡住的那一刻**，却指向被 `CHG-0019` 取代的运维路径 |
| 30d | `ReferenceAttestationInput.verifier` 的 description 仍以旧脚本为例 | 记为 **`0.17.0`** 批次（与 `CHG-0025` 同批）；`0.16.0` 正被 `CHG-0028` 的实现消费，故本轮不动 |
| 31 | ~~质量热力图由 **B1** 消费（浏览器 + 真实 `quality-summary`）~~ | **已执行**，见 §3.31 B：`attempt_count=6`、`unmeasured_runs=6`、安全三态分开（0/2/4）、规则命中表；`mentionsNotImplemented=false` |
| 32 | ~~隔离区复核台由 **B1** 消费（浏览器 + 真实列表端点）~~ | **已执行**，见 §3.31 B：空列表**而非错误**、无横幅，且 `CHG-0021` §2.5 的裁决被逐字渲染在界面上 |
| 33 | 证据包面板由 **B1** 消费（浏览器） | **环境阻塞**：`evaluation_results = 0` ⇒ 没有任何报告 ⇒ 下钻里的面板不渲染（§3.31 C）。**API 层已核过**：对无报告的运行返回 `404` 且 detail 诚实。**不是 B1 缺陷** |
| 34 | ~~四条"尚未实现"假话横幅~~ | **已执行**（B1）：**不是一条而是四条**（它用只读探测确认四个路由都存在），全部删除并改为**由真实 `404` 驱动**的措辞，四处旧断言改反向 + 新增"界面不做进度断言"的断言（§3.31 A） |
| 35 | 交付扫描 `test_evidence.py --all` 的**完整五模块**记录 | **本轮部分执行**：B1 `263`、B3 `393`、B4 `227`、B5 `28` 均已记录（契约 `0.16.0` / `372cfcabad2a…`，全部 `exit=0`），**只差 B2**——其实现代理仍在运行，套件尚不稳定。门禁第 16 条因此保持红色，并在消息里逐模块给出原因与修复命令（§3.32）。**该门禁的成功分支（`check()` 打印并返回 0）也要等 B2 补齐才会第一次被执行** |

---

## 5. 文档影响

| 文档 | 变更 |
|---|---|
| `docs/adr/ADR-0014` | 新增：AI 评分参考的裁决 |
| `docs/adr/ADR-0015` | 新增：参考解答验证的编排归属 |
| `docs/adr/README.md`、`docs/README.md` | ADR 索引 |
| `docs/contracts/changes/CHG-0018` | 新增：契约 0.12.0 → 0.13.0 |
| `docs/contracts/changes/README.md` | CHG 索引 |
| `contracts/CHANGELOG.md`、`contracts/openapi/b2.yaml` | 契约内容 |
| `docs/requirements/rq-catalog.md` | 新增 `RQ-T-30` |
| `docs/requirements/traceability.md` | §3B 按实测改写（含漂移说明） |
| `host/src/autograder_host/checks.py` | `team_expected` 加 `RQ-T-30` |
| `sdk/src/autograder_platform/contracts.py` | 新增 `deployment_root()` |
| `scripts/probe_host_assembly.py` | 修读法 + 加逐模块健康断言 |
| `scripts/render_integration_env.ps1`、`scripts/run_module_process.ps1` | 新增 |
| `scripts/repair_package_digests.py` | 新增：§2.5 的数据修复工具（dry-run 优先，绕过不可变触发器并明确记录理由） |
| `B3/src/autograder_b3/compile/{canonical,compiler}.py` | §2.5：摘要作用域修正 |
| `B3/tests/test_adapters.py` | §2.5：新增摘要作用域守卫测试；`snapshot()` 助手增加 `published_at` |
| `B3/docs/phases/active/phase-06-package-digest-scope.md` | 新增：B3 侧记录 |
| `B3/docs/README.md` | 迁移 head `0003` → `0004`（此前漂移）；基线日期指向 Phase 06 |
| `B2/.gitignore` | 补 `.uv-cache/`（uv 缓存被放进模块目录，此前未忽略） |

---

## 6. 施工期间由模块上报的契约缺口（MAIN 已裁决，待提 CHG）

B2 在实现 `CHG-0014` 的两个隔离区端点时按规程停下上报，**没有自行发明语义**。三处都是真实的
契约缺口，且第一处最重要：

| # | 缺口 | 裁决 | 待办 |
|---|---|---|---|
| 1 | **`resolveIntakeQuarantine` 的"重跑与自动路径相同的校验"在当前数据模型下不可完全实现。** `b2.intake_quarantines` 只存 mailbox/uidvalidity/uid/message_id/reason_code/detail —— **不存报文与附件字节、不存任何摘要**（这是"平台不成为第二个邮件仓库"的直接后果），因此 resolve 时**没有任何可重算的摘要**。要真做只有一条路：resolve 时依赖 IMAP 重取该 UID 的报文再走完整校验；但契约给 resolve 声明的响应集合是 401/403/404/409/422，**没有 503** —— 这反证契约没打算让它依赖外部邮箱 | **采纳"只写归属 + 只重跑当前数据下能重跑的校验"**：角色/作用域判定，外加**"内容类拒绝不可归属"**（`reason_code` 不是匹配类失败时一律 422 —— 命名一个学生修不好"附件被体积上限拒绝"这类问题，允许归属就是给校验开后门） | 把"内容类拒绝不可归属"的 `reason_code` 全集**写进契约**。现在契约只有散文式的"重跑相同校验"，那是一个不可判定的要求，而实现必须猜 |
| 2 | **`intake_quarantines` 没有 `course_id`**，因此"按课程作用域过滤"无法按行判定（匹配失败时课程本身就不存在）；`quality-summary` 同理（题目→课程属 B3） | 采纳 staff 角色 + 列表端点要求令牌至少携带一个课程作用域；**按行过滤不实现**并记为缺口 | 契约要么承认这一层过滤不可能，要么补上关联 |
| 3 | **归属之后不闭环**：resolve 只拿到 `student_id`，而构造提交信封还需要 `course_id`/`section_id`/`question_version_id`，自动路径由匹配规则给出、匹配失败时根本没有 | 采纳"归属只记录、不建提交、不评测"；缺口如实登记 | 若要闭环，需要一条**独立的**"用已归属邮件重建提交"的路径，那是另一个设计而非这个端点的补丁 |

> 三条的共同形状与 `phase-01` 反复记录的那一类相同：**契约写了一句听起来完备的话，而数据模型
> 无法兑现它**。B2 的选择是"实现能兑现的部分 + 把不能兑现的明确登记"，而不是让实现去凑那句话。
> 这是正确的处置，不应被读成"没做完"。

### 6.1 验收口径的一处修正

契约的 `CHG-0014` 验证项 1 要求"构造一封信封摘要不匹配的隔离邮件，手动归属后仍然被拒"。
该验证**在当前数据模型下不可构造**（没有摘要可比），因此它必须被改写为上面裁决 1 的可执行形态，
否则它是一条永远无法通过、也无法被诚实标记为"不适用"的断言。这一条随缺口 1 一并提 CHG。

### 6.2 B1 上报的六项契约歧义：MAIN 裁决

B1 在实现 `CHG-0014`/`CHG-0015` 与 `RQ-T-30` 的界面时停下上报了六处，**没有自行发明语义**。
逐条裁决如下（其中两处需要在契约正文落地，随下一批 CHG，因为它们只改描述、不改线缆格式）：

| # | 歧义 | 裁决 | 理由 |
|---|---|---|---|
| 1 | **`GET /commentary` 的 `404` 语义未成文** —— 契约列了 404，却只描述了 `200 + unavailable` | **采纳 B2 的实现语义并在契约正文写明**：404 = 本部署**有** provider 但该运行**尚未生成**评语；detail 必须说明"不等于运行不存在" | 契约声明了一个状态码却不定义它，与 `CHG-0017` 是同一类（"契约沉默"被当成"契约完备"）。B1 的第一版实现据此把 404 渲染成红色错误框——**下一次也会有消费者这么读** |
| 2 | **`200 + unavailable` 与 `404` 的状态码不对称**（两者都表示"现在没有评语"） | **保留不对称** | 两者语义不同：`unavailable` 是**配置状态**（本部署永远不会有评语），404 是**时序状态**（有 provider，这个运行还没生成、可生成）。统一它们会丢掉"能不能生成"这个信息，而那正是决定要不要显示"生成"按钮的依据 |
| 3 | ~~**`quality-summary` 没有 `security` 段**~~ → **【已推翻，2026-09-20 复核】前提不成立，本项不进 CHG** | **撤回"补进契约"的裁决**。实测：`b2/b3/b4/b5.yaml` **四份都在文档根级**声明了 `security: [{bearerAuth: []}]`，而 operation 级 `security` 只用于**表达例外**（B3 的三个服务间端点用 `serviceToken`、B4 的 `createSession`/`refreshSession`/`getJwks` 用 `[]`）。`getEvaluationQualitySummary` 因此**正确继承**了根级 Bearer，**没有任何遗漏**。`CHG-0006` 的正文第 37–38 行本来就写的是"**根级**新增…默认即需要用户令牌，例外逐条声明"——所以"`CHG-0006` 给全部 operation 加过鉴权声明"这句是**误读**（54 个 operation 里 48 个本就不该有 operation 级 `security`），"这一条是遗漏"也是**误判** | 若给 `quality-summary` 单独补一个 `security: [{bearerAuth: []}]`，它会是**与本文件自身约定相冲突的冗余声明**，并会误导读者以为另外 47 个 operation 也需要同样处理。真正未被契约回答的是**角色可见性**（根级 `bearerAuth` 只说"需要有效令牌"，没说"仅教师/管理员"），那是**另一类**缺口，须另提 CHG 并先确定判定归属 |
| 4 | **`quality-summary` 的"样本很少"没有数值门槛** | **契约不规定数值**；B1 取 5 并在界面标注"这是本页的阅读辅助，不是契约规则" | 门槛是**教学判断**，不是契约事实。写进契约会让它变成对所有消费者的硬约束，而不同课程的可接受样本量不同 |
| 5 | **"参考分与权威分并列"的版式未规定** | **契约不规定版式**；B1 的"同区域、权威分在先、参考分灰底虚线 + idle pill、永不进入任何计算"符合 `ADR-0014` 决策 5 的要求 | 契约管的是"不得让参考分显得更权威"，管不到 flex 布局。把版式写进契约会让它无法演进 |
| 6 | **隔离区归属操作缺少"候选学生"来源** （`GET /users` 仅管理员） | **不新增端点**；教师从自己可见的**名单**取候选，拿不到时如实说明 | 这是既有设计（名单是教师的权威学生来源）。为它开一个平台级用户列表会把"教师能看见谁"这件事从课程作用域里移出来 |

**只有第 1 项**需要契约文本变更（**不改线缆格式**）。第 3 项经复核**前提不成立，已推翻**；
第 4、5、6 项不动契约。
B1 的实现与第 1、2、4、5、6 条裁决一致，无需改动；第 3 项须确认 B1 当时的实际诉求是
"鉴权"还是"角色可见性"——若是后者，按上一格另提 CHG。

---

## 7. 遗留与下一步

1. **等 B2 的工作包结束**（`CHG-0014`/`0015` 四端点的实现），再重装 B2、跑完整门禁、
   重启 host 与三个模块进程、执行 `MAIN/.runtime/acceptance_full.py`。**B1 侧已完成**（§3.9），
   无需再等。
2. **`RQ-T-30` 的实现**（B2 的生成与存储）与其 5 条实现侧断言。B1 的展示侧已实现
   （含"参考分不进入任何本地计算"的反向断言）。**它会改 `B2/api/evaluation_routes.py`，
   而那正是 B2 工作包正在写的文件，因此必须等它结束。**
3. **`CHG-0019`**：把 `ADR-0015` 的裁决落成契约与实现，解除出题发布的 UI 死结。
   它会升到 `0.15.0` 并同步五份锁与四个描述符，同样要等 B2 结束后再做。
   （原文写的 `0.14.0` 已被 `CHG-0020` 占用，故顺延；`CHG-0019` 本体已同步修正。）
4. **§6.2 的第 1 项**（`GET /commentary` 的 `404` 语义未成文）需要契约文本改动；
   与第 3 项（`CHG-0019`）**同一批**落地最省事，因为两处都要动 `b2.yaml`。
   **第 3 项已于 2026-09-20 复核推翻**（见 §6.2 表格），不进本批。
5. **§6 的三处契约缺口已提为 `CHG-0021`**（`resolve` 的可归属原因码全集、按行课程过滤不可实现、
   归属不闭环），并连同施工期新发现的两处**契约写错**（`detail.maxLength` 500 vs 实现与数据库的 1000、
   `resolved` 省略语义的散文与机器语义相反）一并裁决。**`CHG-0021` 与 `CHG-0019` 同批升至 `0.15.0`。**
   其中 `CHG-0014` 的验证项 1 必须在被引用之前改写，否则会留下一条不可执行的断言。
6. ~~**§2.5.2 的结构性问题**需要一个裁决~~ → **已裁决**：
   [ADR-0016](../../adr/ADR-0016-package-digest-freezes-the-artifact.md) 采纳"冻结编译产物、
   不在读时重算"，并列出四个备选方案的否决原因。**实施排在当前集成窗口之后**（见 §3.24），
   因为它需要一次 B3 迁移，而宿主此刻已因 B2 的路由声明而不可重启——**不把第二件事堆进同一个窗口**。
   其"内容不可变"这一半仍由数据库触发器独立保证，未受影响。
7. ~~**`scripts/test_evidence.py --all`** 在套件稳定后录制，让文档里的测试数字可核验。~~ →
   **已完成（2026-09-20）**：五模块证据全部录制并绑定契约 `0.16.0 / 372cfcabad2a…`，
   `check_test_evidence()` 已是 `checks.py` 的第 16 条门禁（见 §3.32、§3.44）。

---

## 5. 目标五项的证据索引（全部为实测读数；截至 2026-09-20 收口）

**标题读数**：`gate_status.py` → **16/16 gates pass**；`python -m autograder_host.checks` → `exit 0`，
且四条文档化判据**逐字命中**（用程序核对了 `AGENTS.md` 里的四条与 `checks.py` 的实际输出）。

| # | 目标里的话 | 证据（读数所在） |
|---|---|---|
| (1) | 修复 MAIN 红门禁并把最终集成序列跑完 | 门禁 `16/16` + 四条判据逐字；台账 B1 `263`、B2 `1298`(skip 9, db 55433)、B3 `393`(55434)、B4 `227`(55435)、B5 `28`，全部 `exit=0`；重装→重启→**证明进程新鲜**（`host_freshness.py` `+22s` `fresh`，§3.43）；`probe_host_assembly` 49 routes / 0 差异 / `restart-ready`；**中继的行为证明**——一次真实提交产生 **7 条**新 outbox 行**全部已发布**、`unpubl` 回到 0（§3.45）；真实端口 E2E：health 四模块健康、SPA `200`、conformance `consistent=True/47 paths/differences=0`、发布工作流四读数、学生可见与隔离（§3.34、§3.40） |
| (2) | 四个契约端点落地并由 B1 消费 | 四端点实测（§3.39）：`intake/quarantines`（空列表非错误）、`…/{id}/resolve`（未知→`404`）、`evaluation-runs/{id}/appeal-pack`（未知→`404`）、`evaluation-runs/quality-summary?question_version_id=`（`200`，草稿与已发布都接受）。**B1 消费由我在真实浏览器里逐页看过**：质量页（§3.47）、隔离区页（§3.48a）、题目/覆盖度页（§3.48b）；其中隔离区页把 `CHG-0021` §2.5 的裁决**连同理由**渲染在界面上，且"始终显式发送 `?resolved=false`"**用电线上的请求验证**过 |
| (3) | 双轨 + AI 评分参考，学生/教师可见 | 契约 `0.13.0`/`CHG-0018`（可空**子对象**、`disclaimers` 恒在非空且覆盖分数参考）；B2 迁移 `0014` 四列 + 表约束同生同死、`max_score` 来自 `case_evidence.weight`、模型自带 `max_score` ⇒ **失败**、越界 ⇒ **生成失败而非截断**（T-ADV-001..011）；B1 的参考分块**只在 `disclaimersPresent` 时渲染**、null 时不渲染且不用 0 代替、与权威分外观互斥，并**有一条测试断言成绩轨道里不出现任何 `advisory` 引用**。**真实端口**：`/commentary` 返回子对象与**三条**标注（第三条**逐字**覆盖参考分），且在 `advisory_score` 为 `null` 时同样成立（§3.38）；**学生** `s.li` 读自己的运行 `200`、读另一学生的运行 **`403` 而非 `404`**（§3.40）；**教师** `t.zhang` 在有课程级授予的课程下读 `200`、在无授予的课程下 `403`——**同一次探测落在边界两侧**，且 `b4.roles` 逐行印证（§3.49） |
| (4) | RQ-T-10/RQ-T-20 界面出口 + 出题发布工作流 | RQ-T-10：真实 provider `ALL PASS`——8 条建议全 `advisory`、3 条 disclaimer、**响应无任何 key 指向受保护内容**、空 `topics` ⇒ `422`、**征求建议不改草稿**（§3.46）。RQ-T-20：`/commentary` 读/生成两端点，生成仅教师/助教/管理员。发布工作流（平台侧编排，已破除 D1 死结）：草稿 + `reference-verifications` → `422` **逐字转述** B3 的 `409`；已发布 → `503` / **85 ms**（死锁修复前 `10.03 s`）；`publish` 无存证 → `422` 指向 "verify and publish"、**不出现**被取代的脚本名、保留 `content_digest` 与失效说明；已发布再 `publish` → `409`（§3.34） |
| (5) | 证据可视化、题型配置器、题库覆盖度 + 真实 provider E2E | 证据可视化：三态安全结论在**响应形状层面**分开（`security_found`/`not_found`/`not_checked`），页面的诚实设计我在浏览器里逐条读过（§3.47）；题型配置器：`B1/src/features/authoring/`（`adapter_kind`、`args`/`stdin`/`expect_exit_code`/`callable`、比较策略 + numeric tolerance、保存前校验），含两处诚实设计（`file` 版本**被拒绝保存**、`adapter-not-readable` 横幅**披露** CHG-0025 缺口）（§3.37）；题库覆盖度：`/app/questions` 的「标签覆盖度」节，明确是**分布不是名单**（刻意不给 `question_id`）（§3.48b）；真实 DeepSeek：出题辅助 `deepseek/deepseek-flash/b3-authoring-v1`（9.3 s 往返）、评语轨 `b2.evaluation_commentaries` 两行 `deepseek/deepseek-flash/b2-commentary/1` 且 `input_scope` 是 22 条字段路径的**正向白名单**（§3.35d、§3.46） |

**三项在本机无法演示（环境事实，不是缺陷；已在 `docs/runbooks/integration.md` §3.9 与 §4 表中记录原因）**：
① 证据包面板的真实渲染（库里从未有过一次**完成**的评测）；② **非空**的 AI 参考分（满分由 `case_evidence`
权重求和填入，没有完成的运行就没有证据）；③ `verify → attest → publish` 完整闭环（需要**真的执行**参考解答，
Windows 无 nsjail，`ADR-0004` 禁止降级为裸进程）。

**本轮（§3.32…§3.48）发现并处置的缺陷**，按"谁的解释与事实不符"归类：
- **运行时的信号说了不成立的话**：`/health/ready` 的"未预期失败"计数（§3.41，已发回 B2）；
- **我自己的记录机制说了不成立的话**：交付扫描不给套件数据库，把 `632 passed / 677 skipped` 记成绿色（§3.44，**已修**）；
- **我自己的解释文字说了不成立的话**：`attempts` 被读反（§3.45，**已修**），以及 `checks.py` 的第四条判据与
  `AGENTS.md` 不一致（§3.44，**已修**）；
- **机制存在却没有执行点**：`test_evidence.py` 从未被任何门禁调用（§3.32，**已修**）；
- **契约/文档的措辞与受约束的事实不符**：草稿 `published_at`（§3.33）、`static_rule_hits` 的两句自相矛盾
  （§3.47）——两项都登记进 `CHG-0025` 的 `0.17.0` 批次，**不在本轮改**（改摘要会让五份证据同时失效）；
- **我自己的测量工具生产了假发现**：UTF-8 被按 Latin-1 解码（§3.38）、必需参数没读就发请求（§3.39）、
  PowerShell 管道与多行 `-Command` 的两个陷阱（§3.43）——四次都表现为"关于被测对象的假结论"。


---

### 3.50 收口：按运行手册 §3.9 跑完序列，门禁回到 16/16 并**稳定**

按 `docs/runbooks/integration.md` §3.9 执行（顺序：重装 → 重启 → 证明新鲜 → 门禁）：

```text
uv sync --all-groups --extra modules --reinstall-package b2/b3/b4/b5   -> Built ×4 / Uninstalled 4 / Installed 4
check_module_installations()                                           -> ok
重启 host / B2 worker / B3 消费（+ B4 relay 一次性）                     -> host ready 0.0s；relay published=0 failed=0 still_pending=0
host_freshness.py --port 18000                                         -> pid 38032 started 20:25:54，最新安装文件 20:25:47，+7s → fresh (exit 0)
gate_status.py --quiet                                                 -> 16/16 gates pass
python -m autograder_host.checks                                       -> 四条判据逐字命中，exit 0
```

**绿色是稳定的，不是时序侥幸**：核对了 `B2/B3/B4/B5` 的 `src/**/*.py` 在重装时刻（`20:25:47`）之后
**没有任何一处被改动**，因此安装副本与工作树一致这件事不会立刻失效。

**B2 给出的可检验预测，在真实端口上逐字验证**（它唯一没验的那条）：§3.41 的修复效果

```text
重启后（无失败）：  evaluation-chain: ok - 评测链可用（取包端点、工作区根与库结构版本均已就绪）
两次故意的沙箱 503（本平台被文档化的预期条件）之后：  detail unchanged = True，counts a sandbox failure? = False
```

即**一个被文档化的平台条件不再污染就绪细节**，而它的文案现在如实写成"累计、仅诊断、不参与健康判定"。
B2 同时把我报的"6 处"更正为实测 **9 处**，并**多找出一处我漏掉的同类**（`events/nats.py:128` 声称
"健康检查据此报 unavailable"，而 `broker_health()` 读的是"配置 + outbox 账本"，从不读那个字段）——
**它纠正了我的计数，也补上了我漏的一处。**

**B1 的标签修复也复验了**：它没有选择过滤（与契约"that is the point of surfacing it"的意图相反），而是把
小标题改成"**静态规则目录与命中次数**"、删掉"契约没有给出规则目录……B1 拿不到规则全集"那句不成立的说明，
并在真实浏览器上复验（15 行全部渲染、`zeroRows = 15`、"拿不到规则全集"已不在页面上），
把我列出的**七条诚实性性质逐条钉成断言**，另加一条覆盖目录语义。

**两个孩子各自提出、并明确没有擅自处置的两项（登记为未决，不属目标五项）**：

1. **隔离区界面无法表达"列出全部"**：`CHG-0021` §2.5 把"不过滤（全部）"定义为**省略** `resolved`，而
   筛选器只有「未处理 / 已处理」两个选项、且页面**每次请求都显式带 `resolved`**（有反向断言）。因此
   契约给出的那种请求形式在界面上**不可达**——教师无法在一个视图里看到全部隔离记录。
   B1 正确地把"永不省略"与另一批次的取舍绑定，没有擅自加第三个选项。**需要一次产品取舍**。
2. **i18n 文案里 49 处 `**` 是字面量**（跨批次、跨页面的展示瑕疵，例如"冻结的\*\*规则目录\*\*"会显示星号）。
   B1 判断属跨批次展示瑕疵，未改。**属待清理项，不是缺陷族。**

**这两项都不影响目标五项的达成**，登记在此以免被漏掉。
