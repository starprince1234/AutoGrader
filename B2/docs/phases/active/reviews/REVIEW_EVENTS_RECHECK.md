# 复核记录：B2「事件 outbox 与 inbox」工作项再裁决（redteam 冷视角）

- 复核者：独立复核者（不修改被测实现）
- **归档注记（2026-09-16，B2 侧）**：本件第 175 行做过**一处机械转义**（表格单元内的 `|` → `\|`，原为
  `grep \`lag|metric\``），目的是让该表格结构合法；**结论文字未改**（仍是"`src/**` 内无 lag 指标"这条缺口判定，
  与 `B2/docs/api/events.md` 的登记一致）。除此之外本件逐字保持复核者原文。
- 工作目录：`D:\VScodeProjects\AutoGrader\B2`
- 被测对象：`src/autograder_b2/events/**`（outbox / dispatcher / payload 门 / nats 总线 / consumer）+ 相关迁移与文档
- 真实依赖：PostgreSQL 17 `localhost:55433`、NATS JetStream `localhost:54223`（均已在跑；**未启动/停止任何容器**）
- 只写位置：`.pytest-tmp/`（已 gitignore）。未改 `src/**`、`docs/**`、`module.yaml`、`contracts.lock`、`alembic/**`。

## 0. 复核计划（一句话）

先用 `git status/log` 判明能否做改动前/后 diff，再读权威契约（`MAIN/docs/api/events.md` §4/§5/§6、`B2/docs/architecture/interfaces.md` §6、asyncapi + envelope schema）与实现源码建立逐条基线，随后**自写探针**（真实 PG + 真实 JetStream，读回路径用 `js.get_msg(stream, seq)` 而非实现者测试的 pull consumer）独立复现 #1/#2/#4 的修复前后行为与 #3/#5 的处置，最后按硬清单逐条给"满足/偏差/缺口"。

## 1. 我实际执行了什么（可复算命令）

```powershell
$env:PYTHONIOENCODING="utf-8"
$env:B2_TEST_DATABASE_URL="postgresql+psycopg://autograder_platform:change-me-local-only@localhost:55433/autograder"
$env:B2_TEST_NATS_URL="nats://localhost:54223"

# (1) 全量基线
uv run pytest -q -p no:warnings
# (2) 我的主探针（72 项断言；真实 DB + 真实 broker）
uv run python .pytest-tmp\reviewer_probe_events.py
# (3) 补充探针（19 项断言；门/必填/DLQ 链/大小/PII）
uv run python .pytest-tmp\reviewer_probe_events2.py
# (4) 逐用例 @db/@broker 标注审计（AST）
uv run python .pytest-tmp\reviewer_marks_audit.py
# (5) 降级实验：broker 不可达
$env:B2_TEST_NATS_URL="nats://127.0.0.1:1"; uv run pytest tests/test_events_outbox.py -q -p no:warnings -rs
# (6) 降级实验：DB 环境变量缺失
Remove-Item Env:B2_TEST_DATABASE_URL; $env:B2_TEST_NATS_URL="nats://localhost:54223"
uv run pytest tests/test_events_outbox.py -q -p no:warnings -rs
# (7) 复核探针文件在降级下的行为
uv run pytest tests/test_events_probe_review.py -q -p no:warnings     # 两种降级各 15 skipped
```

### 全量基线（**未复现你的数字，第一次**）

```
# 第一次
1 failed, 1099 passed, 3 skipped, 1 xfailed in 159.31s
FAILED tests/test_b2_routes.py::test_export_bytes_are_exactly_what_the_renderers_produce
       assert b'PK\x03\x04\...' == b'PK\x03\x04\...'  At index 209 diff: b'C' != b'a'
# 第二次（同一命令、同一环境）
1100 passed, 3 skipped, 1 xfailed in 160.82s
```

→ 你声称的 `1100 passed` 是**第二次才复现**的；第一次失败的是**不稳定用例**。根因我已定位并实测复现（见 §7 N4）。**结论：全量基线不是稳定全绿。**

## 2. 逐条裁决

### #1 `evaluation.failed` 的 `failure_category` 取值域缺 `internal_error` —— **PASS**

实现证据：`src/autograder_b2/events/payload.py:110-120`（`EVALUATION_FAILED_CATEGORY_VALUES = FAILURE_CATEGORY_VALUES + ("internal_error",)`），在 `payload.py:174-181` 只对 `EVALUATION_FAILED` 生效（`"failure_category": _enum(*EVALUATION_FAILED_CATEGORY_VALUES)`，第 179 行）。

探针结论（`.pytest-tmp/reviewer_probe_events.py`，S1/S2/S3）：

| 检查 | 结果 |
|---|---|
| `internal_error` 过门 | PASS（`rejections == []`，S1.1） |
| `static_blocked` 仍被拒 | PASS（`['key failure_category: expected one of: timeout,crash,...,internal_error']`，S1.2） |
| 取值域未被放宽成任意字符串 | PASS（10 个对抗值全拒且**不回显取值**：`totally_unknown_category`/`INTERNAL_ERROR`/`internal_error `（尾空格）/` internal_error`/`Internal_Error`/`""`/源码串/Windows 路径/`ghp_…`，S1.3；非字符串与 `None` 亦拒，S1.4/S1.5） |
| 取值域仍闭合 | PASS（`EVALUATION_FAILED_CATEGORY_VALUES - FAILURE_CATEGORY_VALUES == {"internal_error"}`，S1.7；用例级枚举未被污染，S1.6；`failure_category` 只存在于 `evaluation.failed`，S1.10） |

真实投递核对（真实 DB + 真实 broker，S3）：插入 `failure_category=internal_error` 行 →
```
r1=DispatchReport(selected=1, published=1, failed=0, dead_lettered=0, last_error=None)
stream messages: [(1, 'autograder.v1.evaluation.failed', 527)]
row: attempts=0 published_at=2026-09-16 07:22:08.975+00 dead_lettered_at=None last_error=None
```
且流内 envelope 过 `MAIN/contracts/schemas/event-envelope.schema.json`（我自己读 schema 文件校验，S3.6）、`failure_category=internal_error`（S3.7）、`event_id` 与 outbox 行一致（S3.8）、无敏感标记（S3.9）。

**"原缺陷会导致永不投递→DLQ"这条因果链也被独立验证**（不是照抄你的描述）：把同样会被门拒绝的类别塞进一条真实行，观察其命运（`reviewer_probe_events2.py` A6）：

```
rounds = [(selected=1,published=0,failed=1,dead_lettered=0), (1,0,1,0), (1,0,1,1), (0,0,0,0)]
dead_letters = (DeadLetter(attempts=3, last_error='PayloadRejected: ... key failure_category: expected one of: ...'),)
```
→ 过不了门的行确实每轮失败、到上限进可查询 DLQ；修复后同一形状的行**首次派发即成功**（S3.1）。因果链成立。

### #2 成功后不清 `last_error` —— **PASS**

实现证据：`src/autograder_b2/events/dispatcher.py:207`（`_mark_published` 的 `.values(published_at=utc_now(), last_error=None)`），docstring 见同文件 16-17、192-197。

独立复现（S4）：自造"先失败后成功"行 —— 先用真实不可达地址投递，再换真实 broker：

```
after failure : attempts=1 last_error='EventBusUnavailable: connect failed on nats://<redacted-host> ...' published_at=None dlq=None
after recovery: attempts=1 last_error=None published_at=2026-09-16 07:22:10.151+00 dlq=None stream_msgs=1
```
S4.4（`last_error is None`）、S4.5（published_at 有、DLQ 未动）、S4.6（`attempts` 仍为 1，历史失败次数不丢）、S4.7（消息**真落在流上**，不是只改了 DB 标志）全部 PASS。失败文本已脱敏（S4.2：无口令、`nats://<redacted-host>`、无 `127.0.0.1:1`）。

### #3 inbox 主键 `(consumer, event_id)` vs §5 字面"以 `event_id` 为主键" —— **PASS（处置可接受），但有一条必办后续**

- 契约侧：`MAIN/docs/api/events.md:131`（"inbox 表以 `event_id` 为主键"）与实现不符 —— **偏差属实**。
- 登记属实：`B2/docs/api/events.md:50`（标 `**偏差**`）+ `:60-65`（理由与 CHG 候选）。实现未被偷改（我核了真实目录：`b2.inbox_events` PK 就是复合键，S6.1）。
- 语义不等价的**反例我实测出来了**（S5.17）：同一 `event_id` 由第二个消费者处理时
  `outcome_other = InboxOutcome(duplicate=False, handled=True)`，`inbox rows by consumer = [('b2-question-version-published',1), ('b2-reviewer-second-consumer',1)]` —— 若真按单列 `event_id` 做主键，第二个消费者会把**自己没处理过**的事件判成"已处理"而静默跳过。故复合键是单列键的**严格超集**，实现方向正确，应改的是 §5 的字面（属 MAIN 侧 P4/P2），**不应改实现**。
- **必办后续（形式未闭环）**：`change-protocol.md:26-42`（第 1 步：在 `MAIN/docs/contracts/changes/` 下新建 `CHG-<序号>-<标题>.md`）与 `:137`（"我发现契约与实现不一致 → 提交 CHG，由 MAIN 判定哪边是对的"）。实测：`MAIN/docs/contracts/changes/` 下**只有 `README.md`**（该文件 `:146-148` 仍写"（暂无）"），且 B2 全部文档里**没有任何 "CHG-" 字样**。也就是说：偏差被如实登记在**模块文档**里，但**协议规定的升级载体没有提交**，"交 MAIN 裁定"目前只是一句自我声明。→ 请补一份 CHG（P4/P2，只动 `MAIN/docs/api/events.md` §5，不重算摘要）。

### #4 未识别 `event_type` 只"记录"在返回值里 —— **PASS**

实现证据：`src/autograder_b2/events/consumer.py:144-163`，其中 `147-156` 调 `log_event(logger, "events.inbox.unrecognized_event_type", level=WARNING, fields={event_id, outcome:"ignored", detail})`；模块 docstring `:15-20` 明确"不是只在返回值里体现"。

我走**生产日志路径**（`build_logger("autograder_b2.events.inbox", stream=StringIO)`）+ 真实事务捕获（S5）：

```
[PASS] S5.2 exactly ONE log line emitted :: 1
{"timestamp": "...", "level": "WARNING", "event": "events.inbox.unrecognized_event_type", "producer": "B2",
 "logger": "autograder_b2.events.inbox", "event_id": "2c00ea52-...", "outcome": "ignored",
 "detail": "autograder.v1.question.version.retired"}
```
- 恰好一条（S5.2）；事件名/级别/`event_id`/`outcome`/`detail` 全对（S5.4–S5.7）；未写 inbox 行（S5.3）。
- **不含整包 envelope**：顶级 JSON 键与 envelope 专有键（`schema_version`/`occurred_at`/`correlation_id`/`causation_id`/`payload`/`event_type`）交集为空，且 envelope 序列化串未出现（S5.8）。字段名全在白名单内（S5.9，`logging/redaction.py:54-133`）。
- **不含敏感内容**：把 Windows 绝对路径 + `ghp_` 令牌 + 源码片段塞进 `event_type`（仍满足 `^autograder\.v1\.` 因而能走到该分支，S5.10），日志里
  `redacted_fields: 1, redacted_forbidden_fields: 1`，`detail` 整段被丢弃（`logging/redaction.py:418-419` 的 `looks_like_code` → `_DROPPED_FORBIDDEN`），原始标记零命中（S5.12）。
- **负控**：识别得出的 `event_type` 不产生该日志（S5.14，0 行）且正常处理（S5.15）；重复投递仍只一行 inbox（S5.16）。→ 断言不是恒真。

### #5 文档缺口处置 —— **FAIL（三项声明全部落地，但"足够诚实"这一条不成立）**

先说**已核实为真的部分**：

1. `docs/testing/cases.md:122-150` 新增 T-EVT-001..019，19 行齐全、指向 `tests/test_events_outbox.py`（T-EVT-019 指向 `tests/test_plugin.py`），并在 `:124-125` 写明"逐用例声明依赖"。**PASS**
2. `tests/test_events_outbox.py` 逐用例标注：AST 独立提取 + 两次降级实验吻合 ——
   - broker 不可达：`11 passed, 11 skipped`，跳过集恰为 11 条 `@db @broker`（`test_events_outbox.py:364,400,470,512,553,597,650,708,804,882,993`）；`test_broker_outage_...`（`:923`，**只有 `@db`**）如期**执行并 PASS**。
   - DB 缺失：`8 passed, 14 skipped`，跳过集恰为 14 = 11 条 `@db @broker` + 3 条仅 `@db`（`:787,:923,:1026`）；纯逻辑 4 个函数（含 1 个 ×5 参数化）共 8 条**真的跑了**。
   - **无错标**：模块级 `pytestmark` 已移除（AST 确认 `module pytestmark: (none)`），且降级下"该跑的"全部 PASS、"需要依赖的"整集被跳过 —— 既无"需要 broker 却标成纯逻辑"（那会当场失败），也无"纯逻辑被静默跳过"。8 项均 ≈ 你声称的 11/3/4。**PASS**
3. 横幅与两行修正存在：`docs/requirements/traceability.md:7-12`、`docs/README.md:23-28`；`README.md:43`（`descriptor.events` → `已一致`）、`:41`（结构化日志）。`traceability.md:32-37` 把"已知与初值不符的事实"留在文档里。

**不成立的部分（FAIL 依据）**：

- **行号点名**：`docs/README.md:33` 仍写 `` `POST /api/v1/submissions` 接收 | `待实现` | 尚无契约测试 ``，`:34` 仍写 `` `GET /api/v1/evaluation-runs/{id}/events`（SSE） | `待实现` | 尚无契约测试 ``。而仓库里**确有契约测试**：`tests/test_intake_api.py:1,37,192`（`POST /api/v1/submissions` 的契约/认证/幂等，T-RECV-*，含 `test_valid_submission_is_accepted`）、`tests/test_b2_routes.py:105,1069`（SSE 端点，断言 `content-type: text/event-stream`）。
  "尚无契约测试"是**对事实的肯定性断言**，不是滞后状态词。横幅（`README.md:24-28`）只声明"`待实现` 不等于代码不存在"，并**点名**了"接收（API/邮件）…均已有实现与真实依赖测试"——于是同一页出现**自相矛盾**：横幅说接收有真实测试，行里说没有。这属"把已验证描述成未验证"，与 `module-doc-spec.md:28-38`（铁律二）与你的"不允许把未验证描述成已验证"的反向要求同属一类。
  **改法**：把 `README.md:33` 的"最后验证方式"改为 `tests/test_intake_api.py`（T-RECV-*），`:34` 改为 `tests/test_b2_routes.py`（SSE 用例），或统一改为 `—`（与 35-40 行一致）。两行文字改动，不必等整表刷新。
- **铁律三的落点未满足**：`module-doc-spec.md:38`（铁律三）要求"未通过的验证、外部依赖阻塞、跳过的工作项…留在 `docs/phases/active/` 中"。实测 `docs/phases/active/` **只有 `phase-00-initialization.md`**（4569 B），**没有任何实现阶段的证据记录**；本工作项的缺口（主键偏差、lag、乱序用例、订阅端/派发器无人驱动）都登记在 `docs/api/events.md:56-72` 而非 `docs/phases/active/`。实质"没藏"（诚实性 OK），但**位置不合规**，且因此 `traceability.md:5` 的 `已完成 = 可运行实现 + 通过真实测试 + 阶段证据` 无一条能成立——这正是不敢刷新状态表的根本原因。
  **改法**：新建 `docs/phases/active/phase-01-<...>.md`，写真实命令与结果（本复核记录 §1 的命令可直接复算），再把 §5 的偏差/缺口从中引用过去。
- 同类残留（低）：`tests/test_events_probe_review.py:94` 仍是 `pytestmark = [requires_postgres, requires_jetstream]`，实测两种降级下**各 15 条全跳过**（含两条"断言是否有牙"的证伪探针与 review_1 的门枚举段）——#5 想消除的静默跳过在复核探针文件里依旧存在（该文件自称"复核结束后删除"，但现在确实在交付树里，应删除或同样改为逐用例标注）。

#### #5 附带审查：改写后的探针是更严还是更松？

**必须先说清楚一个无法回避的限制**：本仓库**没有任何提交**（`git log` → `your current branch 'main' does not have any commits yet`；`git status` 显示连 `src/`、`tests/` 都是 untracked），也不存在 `*.orig/*.bak/*~` 备份。**因此"改写前 vs 改写后"的逐行 diff 在本工作区无法取得**，我不能声称我比对过原文。以下是**基于当前文本**的松紧判定 + 用我自己的探针独立复现其断言内容（含负控）。

当前 `tests/test_events_probe_review.py:778-926` 的断言结构：
- `:834-842` 从**源码**解析 `_finish_failure(` 的 `terminal_reason="..."` 字面量，且 `:840` 对"解析不到字面量"**主动 assert 失败**（改成非字面量传参会响铃，而不是静默失守）。我用 AST 独立复核得 `{'infrastructure_error': 319, 'internal_error': 328}`，与它吻合。
- `:859-861` 断言 `internal_error ∈ 可达集`——把"缺陷值确实在范围内"钉住，避免"可达集推导空转"。
- `:862-864` 断言**所有**可达终态原因都过门（`broken == []`）——这**蕴含**"internal_error 过门"，比只断言单个值更强。
- `:871-873` 反方向钉住 `static_blocked` 被拒；`:874-876` 钉住 `security_blocked` 被接受；`:877-880` 用源码文本断言 `_finish_rejected` 内**没有** `self._enqueue(`（我另读 `orchestrator.py:936-985` 确认：该函数确实不发事件）。
- `:921-925` 真实投递：`r1.published >= 1`、流内**恰好 1 条**匹配 envelope、`attempts == 0`、`published_at` 有、`dead_lettered_at`/`last_error` 为空。
- **没有发现"断言被删除/改成恒真"的痕迹**：所有断言都指向可证伪的具体事实；我用独立探针（不同标记串、不同读回路径、不同 schema 校验器）复现了同一批事实（S1/S2/S3 共 30 项），全部一致。
- 两处**比我的探针稍松**（可改，但不是"放松到了恒真"）：
  1. `:921` 用 `r1.published >= 1` 而非 `== 1`（同批只有这一行，风险低）；
  2. 流内匹配条件用 `event_type` + `payload.submission_id` + `payload.terminal_reason`，**没有断言 `payload.failure_category == "internal_error"`**——而 #1 修的正是这个字段。建议补 `and (envelope.get("payload") or {}).get("failure_category") == "internal_error"`（我的 S3.7 已证明该断言会通过）。

## 3. 硬清单逐条对照

### 3.1 `MAIN/docs/api/events.md` §4 禁止内容清单（`:107-124`）—— 满足 8 / 偏差 0 / 缺口 0

实现是**结构性**的：`events/payload.py:141-182` 的逐 subject 白名单 + `:316-336` 的取值形态（UUID / 64-hex 摘要 / 非负整数 / 有限非负分数 / 封闭枚举），**没有任何自由文本键**，因此 §4 八项在投递前即不可表达。逐项：
源码内容、参考答案/`expected_output`、隐藏用例输入、密码/令牌/cookie/密钥、内部绝对路径/主机/端口 —— 均由"无自由文本键 + 形态校验"挡下（探针 S1.3 逐键投毒全拒、S5.12 日志侧同结论）。
学生 PII —— `evaluation.completed` 白名单**不含** `student_id`（`payload.py:167-173`，探针 A5.1），与 §3.4（`:78`）一致；`submission.accepted` 的 `student_id` 是 UUID 伪名（§3.1 `:54` 明确允许）。
堆栈/SQL —— `last_error` 走 `sanitize_error`（`payload.py:372-382` → `logging/redaction.sanitize_text`），实测只落 `类型名: 消息`，无 traceback（S4.2、A6.3）。
**§4 的"提交摘要不进事件"**也成立：`PAYLOAD_KEYS` 全表无 `submission_digest`（探针 A5.2），`result_digest` 是报告摘要而非提交内容摘要。

### 3.2 §5 消费者实现清单（`:128-139`）—— 满足 6 / 偏差 2 / 缺口 2

| §5 条目 | 结论 | 代码/测试证据 |
|---|---|---|
| durable consumer 绑定确切 subject | 满足 | `events/consumer.py:60`（`DEFAULT_CONSUMER`）、`:229-236`（`pull_subscribe(..., durable=durable or self._consumer)`）；`tests/test_events_outbox.py:708`（同 durable 重投） |
| inbox 以 `event_id` 为主键 | **偏差** | 真实目录：`b2.inbox_events` PK = `['consumer','event_id']`（S6.1，`information_schema` 查询）；已登记 `B2/docs/api/events.md:50,60-65`；CHG 未提交（见 #3） |
| 业务去重键二次校验 | **偏差（N/A 未实现）** | 消费者不落 payload、无业务效果（`events/consumer.py:11-14`），该条对本模块为空；文档 `:51` 写"满足"偏乐观，宜写"不适用" |
| 未识别字段容忍 | 满足 | `events/payload.py:284-297`（消费侧校验器去掉 `additionalProperties`，其余照旧）；`tests/test_events_outbox.py:804` |
| 未识别 `event_type` 记录并忽略 | 满足 | `events/consumer.py:144-163`；探针 S5.2/S5.4–S5.8 |
| 失败可重试、超上限进 DLQ 并记录 | 满足 | `consumer.py:193-209`（`nak`/`term`）、`dispatcher.py:212-242`（自增+判上限）、`:143-163`（`dead_letters`）、`:165-190`（`replay`）；探针 A6.1–A6.4 |
| 不读其他模块 schema | 满足 | `events/consumer.py:39-54` 只 import B2 模块 + 契约 envelope schema；无 B3/B4 import |
| 消费延迟（lag）可观测 | **缺口** | `src/**` 内无 lag 指标（grep `lag\|metric` 仅命中无关词）；`B2Runtime` 只报 outbox 积压/DLQ（`runtime.py:243-276`），不是消费 lag；已登记 `B2/docs/api/events.md:56,66-67` |
| 测试含"重复消费"用例 | 满足 | `tests/test_events_outbox.py:512,882`；探针 S5.16 |
| 测试含"乱序到达"用例 | **缺口** | 无用例；已登记 `B2/docs/api/events.md:58,68-69`，理由（唯一订阅 subject 与顺序无关）成立 |

### 3.3 §6 生产者实现清单（`:141-150`）—— 满足 4 / 偏差 3 / 缺口 1

| §6 条目 | 结论 | 证据 |
|---|---|---|
| 事件与业务变更同事务写 outbox | 满足 | `events/outbox.py:70-117`（接调用方 `Connection`）；`orchestrator.py:881-921`、`intake/service.py:312-320` 均在同一 `begin()` 内；`tests/test_events_outbox.py:923`（T-EVT-015） |
| `event_id` 为 **UUID v4** | **偏差（新发现 N1）** | `orchestrator.py:1092`：`event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"b2-evaluation:{deduplication_key}"))` ⇒ 4 个 `evaluation.*` 事件全是 **v5**（探针 S6.2 实测 `version=5`）；只有 `submission.accepted` 走 `outbox.py:58` 的 `uuid.uuid4()`（S6.4）。`MAIN/docs/api/identifiers.md:11` 明确"标识符类型 **UUID v4**…Python 使用 `uuid.uuid4()`"，且"不要使用 UUID v7"（同类非 v4）；B2 文档全库无 `uuid5/v5` 字样 |
| `correlation_id` 取自入站请求；`causation_id` 为直接因果事件 | **偏差（新发现 N3）** | `correlation_id` 满足（`intake/service.py:319` 传 `envelope.correlation_id`；请求 id 见 `intake/api.py:69`）。`causation_id` 在**所有**生产路径恒为 `None`（`orchestrator.py:1090`；`outbox.py:50` 默认），而 asyncapi 对 `evaluation.progress` 的官方示例给的是**非空** `causation_id`（`MAIN/contracts/asyncapi/events.yaml:200`）；B2 文档只在 `docs/api/events.md:76` 说"二者不可混用"，未说明"恒为 null" |
| payload 经对应 JSON Schema 校验后才发出 | **偏差（新发现 N2）** | envelope 确实逐条过契约 schema（`payload.py:259-262`），但 **MAIN 没有 per-event payload schema**（`events.yaml:155-190` 每条 message 都只 `$ref` envelope；envelope 的 `payload` 仅 `{"type":"object"}`，`event-envelope.schema.json:14`），B2 因此自持白名单门。而白名单**不校验必填键**：`{"failure_category":"internal_error"}` 一个键就过门并**真被投递**（探针 A1.1/A2.1–A2.4，流内 298 B，payload 只有 1 个键），未知键仍被拒（A1.2）⇒ 是白名单而非 schema |
| payload 通过 §4 自查（自动化扫描） | 满足（机制不同） | 投递时是**结构性**禁止（比事后扫描更强），测试期另有标记扫描 `tests/test_events_outbox.py:429-467`；源代码里**没有**运行期正则扫描（这是有意的，见 N2/N7） |
| 发布失败进重试、不回滚已成功业务 | 满足 | `dispatcher.py:99-121`（失败留行、`attempts+=1`）+ `:212-242`（DLQ）；`tests/test_events_outbox.py:923`；探针 A6/S4 |
| 事件大小有上限 | **缺口（低）** | 无显式上限与断言；实际由形态决定很小——实测 envelope 298 B（仅 1 键）与 527 B（`evaluation.failed` 全键） |
| `producer` ∈ B1\|B2\|B3\|B4 | 满足（三重防线） | 常量 `domain/events.py:22`（`B2_PRODUCER`）；契约 schema `pattern ^B[1-4]$`（探针 S6.6 拒绝 `B5`）；**DB CHECK** `ck_outbox_events_producer_is_b2` 使外部 producer 连写都写不进（探针 A3.1，`IntegrityError`） |

## 4. 新发现缺陷（不在原 5 条内，按严重度）

**N1（中；硬清单偏差）`evaluation.*` 事件 `event_id` 用 UUID v5，违反 §6 第 2 条 + `identifiers.md:11`**
证据：`orchestrator.py:1092`；探针 S6.2/S6.3（v5、确定性）；S6.4（intake 路径为 v4）。B2 文档零处登记。
改法（择一，且必须走 CHG，不得默默二选一）：(a) 若要保留确定性派生的好处，向 MAIN 提 CHG 修改 §6 第 2 条并说明与 `identifiers.md:11` 的关系；(b) 改实现为 `uuid4()`，把幂等完全交给已有的 `(subject, deduplication_key)` 唯一索引（`alembic` 0004 已建）+ `Nats-Msg-Id`，并补一条"重放同一去重键不产生第二条事件"的真实测试。

**N2（中）payload 门不校验必填键；契约层无 per-event payload schema**
证据：A1.1（缺键过门）、A2.3（流内 payload 只有 `failure_category`）、A2.4（仍过 envelope schema）、`events.yaml:155-190`、`event-envelope.schema.json:14`。B2 文档未登记；该行为只在 `tests/test_events_probe_review.py:767-773` 被"记录"。
影响：写入端一旦漏键（如缺 `evaluation_run_id`），事件会**照发**，B3 按 `evaluation_run_id` 去重统计（§3.4 `:80`）会失配，且不会有任何告警。
改法：B2 侧把键集合升级为"必填 + 可选"两级（在 `PAYLOAD_KEY_SOURCES` 旁显式声明 required 集合并校验），并向 MAIN 提 CHG 请其补 per-event payload schema；无论走哪条，都要在 `docs/api/events.md` 登记"当前不校验必填"。

**N3（中）`causation_id` 生产路径恒为 null，与 §6 第 3 条、asyncapi 示例不一致且未文档化**
证据：`orchestrator.py:1090`、`outbox.py:50`；`events.yaml:200`（progress 官方示例非空）；`docs/api/events.md:76`。
改法：要么让 `started/progress/completed|failed` 链填 `causation_id`（progress 直接因果于同 run 的 started，符合 §6 第 3 条与示例），要么在 B2 文档明确"B2 的事件为事实事件，`causation_id` 恒为 null，理由…"，并提 CHG 让 MAIN 决定哪种语义为准。

**N4（中；使"全量基线全绿"不成立）XLSX 导出字节不确定 ⇒ `tests/test_b2_routes.py:1412` 不稳定**
证据：我第一次全量 `1 failed, 1099 passed`（失败点 `test_export_bytes_are_exactly_what_the_renderers_produce`，`At index 209 diff: b'C' != b'a'`），第二次 `1100 passed`；单独跑 3 次全过。根因实测复现：`render_xlsx` 把**墙钟时间**写进产物 ——
```
a: <dcterms:created ...>2026-09-16T07:26:28Z  b: ...2026-09-16T07:26:29Z
ZIP entry date_time ('docProps/core.xml', (2026,9,16,15,26,28))
back-to-back identical: True ; sleep(1.3) 后 identical: False
```
改法：让 `render_xlsx` 的 `docProps` 时间与 ZIP 条目时间取**固定值**（或取业务时间 `submitted_at`/`occurred_at`），使同一输入恒同字节；或把该用例的比较改为"解析后逐单元格相等"。**该缺陷不属于 events 工作项**，但它直接推翻"基线稳定全绿"这一前提，且任何后续工作项都可能被它干扰。

**N5（低-中）`tests/test_events_probe_review.py:94` 仍是模块级 `pytestmark` ⇒ 降级下 15 条全跳过**
证据：实测 broker 不可达 → `15 skipped`；DB 缺失 → `15 skipped`；含 `test_review_a_schema_check_has_teeth`、`test_review_a_payload_whitelist_has_teeth` 两条"断言是否有牙"的证伪探针与 review_1 的门枚举段。
改法：删除该文件（其自述"复核结束后删除"），或按 `test_events_outbox.py:120-128` 的写法改为逐用例 `@db`/`@broker`。

**N6（低）`evaluation.failed` 门接受 `security_blocked` 作 `failure_category`，而 §3.5 禁止该事件表达"安全被拒"**
证据：`payload.py:93-102`（`FAILURE_CATEGORY_VALUES` 含 `security_blocked`）→ `:120` → `:179`；探针 S2.8（过门）。生产侧**不可达**（`_finish_rejected` 不发事件，`orchestrator.py:936-985`；探针断言 `"self._enqueue(" not in rejected_body` 亦成立），故是"容许"而非"已发生"。改法：在 `evaluation.failed` 的枚举里剔除 `security_blocked`（用例级语义仍由报告 schema 承载），或在文档写明为何容许并让 MAIN 确认。

**N7（低）B2 文档没有 §6（生产者清单）的逐条对照，也未描述 I32 的 payload 门本体**
证据：全 `docs/**` grep `PAYLOAD_KEYS|键完整|生产者的实现清单` 无命中；`I32` 只在 `interfaces.md:375` 与 `cases.md:128` 出现（无实现说明）。⇒ 读者无法从文档得知"允许哪些键/哪些必填/缺键会怎样"，而 `docs/api/events.md` 只登记了 §5（消费者）。
改法：在 `docs/api/events.md` 增一节"§6 生产者清单逐条对照 + payload 门",并在其中登记 N1/N2/N6/N7 的现状。

**N8（低）`docs/README.md:33-34` 的"尚无契约测试"与仓库现实矛盾**（详见 #5，含改法）。

**N9（低）无显式事件大小上限/断言**（§6 第 7 条）；实测 298–527 B，风险低，但既无上限也无文档。

**N10（观察，越界）`docs/phases/active/` 只有 phase-00；且其 `:33-35` 仍记 `uv` 为"未执行（无法写缓存目录）"，与当前环境矛盾** —— 我已用 `uv run pytest/ python` 跑通全部复核命令。不属本工作项，但说明"证据记录"整体缺失。

## 5. 我实际跑了什么 / 没跑成什么（诚实交代）

跑成了：
- 全量 `uv run pytest -q -p no:warnings` **两次**（1 failed / 1100 passed，见 N4）；`uv run ruff check .` → `All checks passed!`；我的两个自写探针共 **91 项断言全部 PASS**（72 + 19）；两次降级实验；探针文件降级实验；`information_schema`/`pg_constraint` 真实目录查询；自己读 `MAIN` 的 schema 文件做独立校验；AST 独立推导生产端 payload 键集合与终态原因集合。
没跑成 / 做不到：
- **改写前 vs 改写后的逐行 diff**：仓库无任何提交、无备份文件（`git log` 报 "does not have any commits yet"）。我因此只在当前文本上判松紧，并用独立探针复现其断言内容；**不声称比对过原文**。
- 未启动/停止任何容器（约束）；未修改任何被测文件；未验证 Windows 上无法验收的 nsjail/Linux 部分（与本工作项无关）。
- `docs/phases/active/` 无实现阶段记录可读，因此"实现阶段证据"这一环我无从核对（记为 N10）。

## 6. 一句话总评

**不能无条件判"通过"**：#1/#2/#4 的修复我独立复现为真（含负控与真实投递/DLQ 链），#3 的方向正确但 CHG 载体未提交，#5 三项声明均已落地却仍留下 2 行与现实矛盾的 README 行且缺口未按铁律三落到 `docs/phases/active/`；硬清单 §4 8/8 满足，§5 6 满足/2 偏差/2 缺口，§6 4 满足/3 偏差/1 缺口，并有 N1（uuid5，硬清单偏差）、N2（必填键不校验）、N3（causation_id 恒 null）、N4（导出字节不确定导致基线不稳定）四项需先处置/登记。**建议：判"有条件通过"——修完 #5 的两行 + 建阶段证据记录 + 为 N1/N2/N3 提交 CHG 或登记偏差后转通过。**
